"""Guided static-letter collection. Reads UART only; never trains or flashes."""

import argparse
from datetime import datetime, timezone
import hashlib
import io
import json
import os
from pathlib import Path
import queue
import re
import select
import sys
import threading
import time
import uuid
import zlib

from tools.asl_contract import STATIC_CLASSES
from tools.capture_camera import SerialStream, read_packet, as_image, checked_identity, listening_port

READY=b'COLLECT READY v=5 settle_ms=2500 payload_bytes=49152; no UART RX\n'
BEGIN=re.compile(rb'HGCONT BEGIN 5 ([0-9]{1,10})\n')
END=re.compile(rb'HGCONT END 5 ([0-9]{1,10}) ([0-9]{1,10})\n')


def identifier(value):
    if not isinstance(value,str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,63}',value):
        raise ValueError('Use a simple person/session identifier (letters, digits, dash, underscore)')
    return value


def utc(): return datetime.now(timezone.utc).isoformat()


def read_ready(stream):
    log=bytearray()
    while True:
        line=stream.readline(512);log.extend(line)
        if len(log)>65536:raise ValueError('No continuous-collection boot marker within 64 KiB; wrong firmware')
        if b'FAIL' in line or b'FAULT' in line:raise ValueError('Board reported a boot failure')
        if line.startswith(b'CAPTURE READY') or b'LIVE ASL START' in line:
            raise ValueError('Wrong firmware: use the continuous collector, not capture v4 or live ASL')
        if line.startswith(b'COLLECT READY'):
            if (line!=READY or b'HandGPT CONTINUOUS COLLECTION v=5;' not in log or
                    not re.search(rb'COEXIST SELFTEST PASS [^\n]*checked=1000\n',log)):
                raise ValueError('Missing/mismatched collector profile or full stored-image boot self-test')
            return bytes(log)


def read_cycle(stream, previous_sequence=0, previous_frame=0, on_begin=None):
    line=stream.readline(160);begin=BEGIN.fullmatch(line)
    if not begin:raise ValueError('Expected continuous BEGIN; stream corrupt or board reset')
    sequence=int(begin[1])
    if sequence!=previous_sequence+1 or sequence>0xffffffff:
        raise ValueError('Nonconsecutive/repeated collection cycle; reset and start a new session')
    token=on_begin(sequence) if on_begin else None
    header=stream.readline(160)
    stream.pending[:0]=header
    payload,metadata=read_packet(stream)
    if (metadata['wire_version'],metadata['width'],metadata['height'],len(payload))!=(2,128,128,49152):
        raise ValueError('Collector requires the unchanged full-view 128x128 packet')
    if metadata['frame']<=previous_frame:raise ValueError('Camera frame counter repeated/wrapped')
    tail=stream.readline(160);end=END.fullmatch(tail)
    if not end or (int(end[1]),int(end[2]))!=(sequence,metadata['frame']):
        raise ValueError('Continuous cycle/frame END mismatch')
    metadata.update(capture_protocol=5,collection_cycle=sequence)
    trailer=f"\nHGFRAME END {metadata['frame']} {metadata['crc32']}\n".encode()
    return payload,metadata,line+header+trailer+tail,token


class Stopped(Exception): pass


class InterruptibleStream(SerialStream):
    def __init__(self,fd,deadline,stop):
        super().__init__(fd,deadline);self.stop=stop

    def read(self,count):
        if count<=0:return b''
        if self.stop.is_set():raise Stopped()
        if self.pending:
            result=bytes(self.pending[:count]);del self.pending[:count];return result
        while not self.stop.is_set():
            remaining=self.deadline-time.monotonic()
            if remaining<=0:raise TimeoutError('Collector UART deadline expired')
            if not select.select([self.fd],[],[],min(remaining,0.2))[0]:continue
            try:chunk=os.read(self.fd,count)
            except BlockingIOError:continue
            if not chunk:raise OSError('UART disconnected')
            self.received+=len(chunk);self.recent.extend(chunk);del self.recent[:-2048]
            return chunk
        raise Stopped()


class Collector:
    """One reader always drains UART, including while the user answers prompts.

    Label is snapshotted at BEGIN, never after payload arrival. Every arm skips
    a complete CRC-valid cycle before accepting a subsequent BEGIN. No buffered
    result is relabeled. This is a freshness guard, not a wire-clock guarantee;
    users still review the exact saved image before accepting training labels.
    """
    def __init__(self,stream,timeout=30,on_begin=None):
        self.stream,self.timeout,self.on_begin=stream,timeout,on_begin
        self.lock=threading.Lock();self.results=queue.Queue(maxsize=4)
        self.ready=threading.Event();self.error=None;self.boot=b''
        self.generation=0;self.remaining=0;self.skip=0;self.label=None;self.guard=None
        self.thread=threading.Thread(target=self.run,daemon=True)

    def start(self):self.thread.start()

    def arm(self,label,count):
        if label not in STATIC_CLASSES or type(count) is not int or not 1<=count<=200:
            raise ValueError('Invalid static label or capture count')
        with self.lock:
            if not self.ready.is_set() or self.error:raise ValueError('Collector is not ready')
            if self.remaining or not self.results.empty():raise ValueError('Previous label block is unfinished')
            self.generation+=1;self.label=label;self.remaining=count;self.skip=1;self.guard=None

    def begin(self,sequence):
        with self.lock:
            if not self.remaining:return None
            if self.skip:
                self.skip-=1;self.guard=sequence;return None
            token=(self.generation,self.label,self.guard)
        if self.on_begin:self.on_begin(sequence,token[1])
        return token

    def deliver(self,payload,metadata,log,token):
        if token is None:return
        with self.lock:
            generation,label,guard=token
            if generation!=self.generation:raise ValueError('Label changed during an in-flight capture')
            if guard is None or guard>=metadata['collection_cycle']:raise ValueError('No complete post-arm warmup cycle')
            metadata=dict(metadata,label=label,warmup_cycle=guard,received_at=utc())
            self.results.put_nowait((payload,metadata,self.boot+log)) # Overflow stops rather than silently dropping labels.
            self.remaining-=1

    def run(self):
        try:
            self.boot=read_ready(self.stream);self.ready.set()
            sequence=frame=0
            while True:
                self.stream.deadline=time.monotonic()+self.timeout
                payload,metadata,log,token=read_cycle(self.stream,sequence,frame,self.begin)
                sequence,frame=metadata['collection_cycle'],metadata['frame']
                self.deliver(payload,metadata,log,token)
        except Stopped:pass
        except Exception as error:self.error=error;self.ready.set()

    def result(self):
        deadline=time.monotonic()+3*self.timeout
        while time.monotonic()<deadline:
            if self.error:raise self.error
            try:return self.results.get(timeout=0.2)
            except queue.Empty:continue
        raise TimeoutError('No complete post-arm capture received')


def save_capture(session_dir,person,session,hand,payload,metadata,log,connection):
    identifier(person);identifier(session)
    label=metadata['label']
    if label not in STATIC_CLASSES or hand not in ('left','right','unspecified'):
        raise ValueError('Invalid label/hand')
    if (metadata.get('capture_protocol'),metadata.get('wire_version'),metadata.get('width'),metadata.get('height'),len(payload))!=(5,2,128,128,49152):
        raise ValueError('Unexpected collection payload/profile')
    if f'{zlib.crc32(payload):08x}'!=metadata['crc32']:raise ValueError('Payload changed before save')
    image=as_image(payload,128,128)
    directory=Path(session_dir)/label
    if directory.is_symlink():raise ValueError('Label directory must not be a symlink')
    directory.mkdir(exist_ok=True)
    png=io.BytesIO();image.save(png,format='PNG');png=png.getvalue()
    base=directory/(datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'-'+uuid.uuid4().hex[:12])
    record=dict(metadata,schema=2,kind='continuous_static_camera_capture',person=person,session=session,hand=hand,
                connection_id=connection,captured_at=utc(),split='unassigned',label_reviewed=False,
                source='E8 continuous collector; unrotated camera pixels; host-supplied label requires review',
                raw_sha256=hashlib.sha256(payload).hexdigest(),png_sha256=hashlib.sha256(png).hexdigest())
    for suffix,content in (('.rgb',payload),('.png',png),('.log',log),('.json',(json.dumps(record,indent=2)+'\n').encode())):
        with base.with_suffix(suffix).open('xb') as f:f.write(content)
    return base.with_suffix('.png')


def collect(args):
    from PIL import Image # Check the isolated image dependency before hardware access.
    identifier(args.person);identifier(args.session)
    if len(set(args.labels))!=len(args.labels):raise ValueError('Do not repeat labels within a session')
    if not sys.stdin.isatty():raise ValueError('Collection requires an interactive terminal')
    identity=checked_identity(args.port)
    root=args.output.resolve();person_dir=root/args.person
    if person_dir.is_symlink():raise ValueError('Person directory must not be a symlink')
    person_dir.mkdir(parents=True,exist_ok=True)
    session_dir=person_dir/args.session
    session_dir.mkdir(exist_ok=False) # Never mix people or reuse an existing session.
    connection=uuid.uuid4().hex
    with (session_dir/'session.json').open('x') as f:
        json.dump(dict(schema=1,person=args.person,session=args.session,hand=args.hand,labels=args.labels,
                       per_label=args.per_label,connection_id=connection,started_at=utc(),notes=args.notes),f,indent=2)
    stop=threading.Event();paths=[];complete=False
    try:
        with listening_port(args.port,identity) as fd:
            stream=InterruptibleStream(fd,time.monotonic()+args.ready_timeout,stop)
            reader=Collector(stream,args.timeout,lambda n,label:print(f'Cycle {n}: {label} — settle window 2.5s; hold until image saved.',flush=True))
            print('READ ONLY — press board RESET once now. No UART requests are sent.',flush=True)
            reader.start()
            try:
                if not reader.ready.wait(args.ready_timeout+1):raise TimeoutError('No collection boot; wrong firmware or reset missed')
                if reader.error:raise reader.error
                print('Boot self-test/profile verified. Receiver keeps draining while you answer prompts.')
                for label in args.labels:
                    if label=='OTHER':print('OTHER: unsupported/partial/transitional poses, NOT deliberately formed supported letters.')
                    input(f'\nPerson {args.person}; label {label}; {args.per_label} images. Form the pose, then Enter (Ctrl-C stops): ')
                    reader.arm(label,args.per_label)
                    print('One complete cycle is discarded after arming. Keep the pose; vary placement during moving preview.',flush=True)
                    for index in range(args.per_label):
                        payload,metadata,log=reader.result()
                        if metadata['label']!=label:raise ValueError('Received a result for the wrong label block')
                        path=save_capture(session_dir,args.person,args.session,args.hand,payload,metadata,log,connection)
                        paths.append(str(path.relative_to(root)))
                        print(f'CRC OK {label} {index+1}/{args.per_label}: {path}',flush=True)
                complete=True
            finally:
                stop.set();reader.thread.join(timeout=2)
                if reader.thread.is_alive():raise RuntimeError('Serial reader failed to stop safely')
    finally:
        with (session_dir/('complete.json' if complete else 'interrupted.json')).open('x') as f:
            json.dump(dict(status='complete' if complete else 'interrupted',saved=paths,finished_at=utc(),
                           label_review_required=True),f,indent=2)
        print(f'Saved {len(paths)} images in {session_dir}. Labels are NOT reviewed; audit/contact-sheet review required.')


def main():
    parser=argparse.ArgumentParser(description=__doc__,allow_abbrev=False)
    parser.add_argument('--port',required=True)
    parser.add_argument('--person',required=True,help='Pseudonymous signer ID; reuse the same ID across their sessions')
    parser.add_argument('--session',required=True,help='New session ID; existing sessions are never overwritten')
    parser.add_argument('--hand',choices=('left','right','unspecified'),default='unspecified')
    parser.add_argument('--labels',nargs='+',choices=STATIC_CLASSES,default=list(STATIC_CLASSES))
    parser.add_argument('--per-label',type=int,default=5)
    parser.add_argument('--output',type=Path,default=Path('.work/asl/continuous-camera'))
    parser.add_argument('--ready-timeout',type=float,default=30)
    parser.add_argument('--timeout',type=float,default=30)
    parser.add_argument('--notes',default='')
    args=parser.parse_args()
    if not 1<=args.per_label<=200 or not 5<=args.ready_timeout<=300 or not 10<=args.timeout<=60:
        parser.error('Use 1..200 images/label, ready timeout 5..300s, packet timeout 10..60s')
    try:collect(args)
    except KeyboardInterrupt:print('Stopped. Completed captures retained; use a NEW session name to resume.');return 130
    except (OSError,ValueError,RuntimeError,TimeoutError,queue.Full) as error:parser.exit(1,f'ERROR: {error}\n')
    return 0


if __name__=='__main__':raise SystemExit(main())
