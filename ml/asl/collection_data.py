"""Audit continuous captures and generate review sheets. Never trains or evaluates a model."""

import argparse
from collections import Counter
import hashlib
import io
import json
from pathlib import Path
import zlib

from PIL import Image, ImageDraw

from tools.asl_contract import STATIC_CLASSES
from tools.collect_camera import identifier, read_cycle, READY
from tools.capture_camera import SerialStream, as_image
from .data import sha256, dhash, HashIndex


class MemoryStream(SerialStream):
    def __init__(self,data):
        self.source=io.BytesIO(data);self.pending=bytearray()

    def read(self,count):
        if count<=0:return b''
        if self.pending:
            chunk=bytes(self.pending[:count]);del self.pending[:count];return chunk
        chunk=self.source.read(count)
        if not chunk:raise ValueError('Truncated collection log/packet')
        return chunk


def inventory(root, selected_sessions=None):
    root=Path(root).resolve(strict=True)
    allowed=None if selected_sessions is None else {(identifier(p),identifier(s)) for p,s in selected_sessions}
    if allowed is not None and not allowed:raise ValueError('An explicit session snapshot cannot be empty')
    records=[];sessions=[]
    for person_dir in sorted(root.iterdir()):
        if allowed is not None and person_dir.name not in {p for p,_ in allowed}:continue
        if person_dir.is_symlink() or not person_dir.is_dir():raise ValueError('Unexpected person directory')
        person=identifier(person_dir.name)
        for directory in sorted(person_dir.iterdir()):
            if allowed is not None and (person,directory.name) not in allowed:continue
            if directory.is_symlink() or not directory.is_dir():raise ValueError('Unexpected session directory')
            session=identifier(directory.name)
            if (directory/'session.json').is_symlink():raise ValueError('Symlinked session metadata')
            config=json.loads((directory/'session.json').read_text())
            if (config.get('schema')!=1 or config.get('person')!=person or config.get('session')!=session or
                    config.get('hand') not in ('left','right','unspecified') or
                    not config.get('connection_id') or not config.get('labels') or
                    len(set(config['labels']))!=len(config['labels']) or
                    any(c not in STATIC_CLASSES for c in config['labels']) or
                    type(config.get('per_label')) is not int or not 1<=config['per_label']<=200):
                raise ValueError('Session person/label/hand/config mismatch')
            outcomes=[p for p in (directory/'complete.json',directory/'interrupted.json') if p.exists()]
            if len(outcomes)!=1 or outcomes[0].is_symlink():raise ValueError('Session is still active or has ambiguous completion records')
            outcome=json.loads(outcomes[0].read_text())
            if outcome.get('status')!=outcomes[0].stem or outcome.get('label_review_required') is not True:
                raise ValueError('Invalid completion status')
            seen_cycles=set();seen_frames=set();session_rows=[]
            for label_dir in sorted(directory.iterdir()):
                if label_dir.name in ('session.json',outcomes[0].name):continue
                if label_dir.is_symlink() or not label_dir.is_dir() or label_dir.name not in config['labels']:
                    raise ValueError('Unexpected or symlinked label entry')
                stems=set()
                for path in label_dir.iterdir():
                    if path.is_symlink() or not path.is_file() or path.suffix not in ('.rgb','.png','.json','.log'):
                        raise ValueError('Unexpected capture sidecar')
                    stems.add(path.stem)
                for stem in sorted(stems):
                    base=label_dir/stem
                    files={s:base.with_suffix(s) for s in ('.rgb','.png','.json','.log')}
                    if any(not p.is_file() for p in files.values()):raise ValueError('Incomplete capture group: '+str(base))
                    m=json.loads(files['.json'].read_text());raw=files['.rgb'].read_bytes();log=files['.log'].read_bytes()
                    if (m.get('schema')!=2 or m.get('kind')!='continuous_static_camera_capture' or
                            (m.get('person'),m.get('session'),m.get('label'),m.get('hand'),m.get('connection_id'))!=
                            (person,session,label_dir.name,config['hand'],config['connection_id']) or
                            m.get('split')!='unassigned' or m.get('label_reviewed') is not False or
                            (m.get('capture_protocol'),m.get('wire_version'),m.get('width'),m.get('height'),len(raw))!=(5,2,128,128,49152) or
                            (m.get('source_width'),m.get('source_height'),m.get('crop'),m.get('resize'),m.get('format'))!=
                            (480,480,'none','PIL_bilinear_22bit','RGB888P')):
                        raise ValueError('Capture metadata/path/profile mismatch: '+str(base))
                    if (sha256(files['.rgb'])!=m.get('raw_sha256') or sha256(files['.png'])!=m.get('png_sha256') or
                            f'{zlib.crc32(raw):08x}'!=m.get('crc32')):raise ValueError('Capture hash/CRC mismatch')
                    cycle,frame,guard=m.get('collection_cycle'),m.get('frame'),m.get('warmup_cycle')
                    if (any(type(x) is not int or not 0<x<=0xffffffff for x in (cycle,frame,guard)) or guard>=cycle or
                            cycle in seen_cycles or frame in seen_frames):raise ValueError('Invalid/repeated collection cycle/frame/warmup')
                    seen_cycles.add(cycle);seen_frames.add(frame)
                    # Reconstruct and validate the exact wire packet using raw bytes,
                    # not only sidecar claims. Log contains headers/trailers, not pixels.
                    marker=f'HGCONT BEGIN 5 {cycle}\n'.encode()
                    # The listener may have drained a previous run before the
                    # user's RESET. Those bytes can repeat the current cycle ID.
                    # Preserve the log, but validate only the last complete boot
                    # and the exact frame suffix saved after its READY marker.
                    ready_end=log.rfind(READY)+len(READY) if READY in log else -1
                    banner=b'HandGPT CONTINUOUS COLLECTION v=5;'
                    boot_start=log.rfind(banner,0,ready_end) if ready_end>=0 else -1
                    if boot_start<0 or not log[ready_end:].startswith(marker) or log[ready_end:].count(marker)!=1:
                        raise ValueError('Missing/ambiguous collection log')
                    boot=log[boot_start:ready_end]
                    wire=log[ready_end+len(marker):]
                    from tools.collect_camera import read_ready
                    read_ready(MemoryStream(boot))
                    header,tail=wire.split(b'\n',1)
                    payload,decoded,_,_=read_cycle(MemoryStream(marker+header+b'\n'+raw+tail),cycle-1,0)
                    canonical=(f"HGFRAME 2 128 128 RGB888P 49152 {frame} {m['crc32']}\n\n"
                               f"HGFRAME END {frame} {m['crc32']}\nHGCONT END 5 {cycle} {frame}\n").encode()
                    if wire!=canonical:raise ValueError('Noncanonical/trailing collection packet log')
                    if payload!=raw or decoded['frame']!=frame or decoded['crc32']!=m['crc32']:
                        raise ValueError('Wire/sidecar frame mismatch')
                    with Image.open(files['.png']) as image:
                        image.load()
                        if image.mode!='RGB' or image.size!=(128,128) or image.tobytes()!=as_image(raw,128,128).tobytes():
                            raise ValueError('PNG/raw pixels differ')
                        fingerprint=dhash(image);pixels=hashlib.sha256(image.tobytes()).hexdigest()
                    session_rows.append(dict(path=str(files['.png'].relative_to(root)),person=person,session=session,
                        label=label_dir.name,hand=config['hand'],connection_id=config['connection_id'],
                        collection_cycle=cycle,frame=frame,sha256=m['png_sha256'],raw_sha256=m['raw_sha256'],
                        metadata_sha256=sha256(files['.json']),log_sha256=sha256(files['.log']),normalized_sha256=pixels,
                        dhash=fingerprint,capture_protocol=5))
            ordered=sorted(session_rows,key=lambda r:r['collection_cycle'])
            if any(b['frame']<=a['frame'] for a,b in zip(ordered,ordered[1:])):raise ValueError('Frame order reversed')
            counts=Counter(r['label'] for r in session_rows)
            if any(n>config['per_label'] for n in counts.values()):raise ValueError('More images than requested')
            if outcome['status']=='complete' and any(counts[c]!=config['per_label'] for c in config['labels']):
                raise ValueError('Complete session is missing requested images')
            if (len(outcome.get('saved',[]))!=len(session_rows) or
                    set(outcome.get('saved',[]))!={r['path'] for r in session_rows}):raise ValueError('Completion inventory differs from actual files')
            sessions.append(dict(person=person,session=session,status=outcome['status'],counts=dict(counts),
                config_sha256=sha256(directory/'session.json'),completion_sha256=sha256(outcomes[0])))
            records.extend(session_rows)
    if allowed is not None and {(s['person'],s['session']) for s in sessions}!=allowed:
        raise ValueError('A snapshotted collection session is missing')
    if not records:raise ValueError('No completed capture groups to review')
    return records,sessions


def split_records(records,train_people=(),val_people=(),test_people=()):
    groups=[set(g) for g in (train_people,val_people,test_people)]
    if any(len(g)!=len(set(g)) for g in (train_people,val_people,test_people)) or any(groups[i]&groups[j] for i in range(3) for j in range(i)):
        raise ValueError('A person cannot cross train/validation/test assignments')
    people={r['person'] for r in records};assigned=set().union(*groups)
    if assigned and (assigned!=people or not groups[0] or not groups[1]):
        raise ValueError('Assign EVERY captured person exactly once, with distinct training and validation people')
    split_by_person={p:s for s,g in zip(('train','val','test'),groups) for p in g}
    pixels={}
    for r in records:pixels.setdefault(r['normalized_sha256'],set()).add(r['label'])
    retained=[];quarantined=[];index=HashIndex()
    for split in (('train','val','test') if assigned else ('unassigned',)):
        rows=[dict(r,split=split) for r in records if split_by_person.get(r['person'],'unassigned')==split]
        kept=[]
        for row in rows:
            if len(pixels[row['normalized_sha256']])>1:
                quarantined.append(dict(row,reason='identical_pixels_conflicting_labels'));continue
            if split in ('val','test') and index.contains_near(row['dhash']):
                quarantined.append(dict(row,reason='near_duplicate_of_earlier_split'));continue
            kept.append(row)
        for row in kept:index.add(row['dhash'])
        retained.extend(kept)
    return retained,quarantined


def audit(args):
    if args.output.exists():raise ValueError('Choose a new audit output; never overwrite reviews')
    root=args.root.resolve(strict=True);records,sessions=inventory(root)
    retained,quarantined=split_records(records,args.train_person,args.val_person,args.test_person)
    args.output.mkdir(parents=True)
    manifest=dict(schema=1,kind='continuous_static_camera_pending_review',status='pending_label_review',
        root=str(root),classes=list(STATIC_CLASSES),sessions=sessions,records=retained,quarantined=quarantined,
        person_assignments=dict(train=args.train_person,val=args.val_person,test=args.test_person),
        unique_pixel_images=len({r['normalized_sha256'] for r in records}),
        counts={s:{c:sum(r['split']==s and r['label']==c for r in retained) for c in STATIC_CLASSES} for s in ('unassigned','train','val','test')},
        limitations=['Pending human label review; NOT a training-approved manifest.',
                     'dHash screening is heuristic; many similar frames are not independent examples.',
                     'No model training or final-test evaluation performed.'])
    with (args.output/'manifest.json').open('x') as f:json.dump(manifest,f,indent=2);f.write('\n')
    review=args.output/'review';review.mkdir()
    pages=[]
    for label in STATIC_CLASSES:
        rows=[r for r in records if r['label']==label]
        for offset in range(0,len(rows),64):
            chunk=rows[offset:offset+64]
            sheet=Image.new('RGB',(8*144,((len(chunk)+7)//8)*172),'white');draw=ImageDraw.Draw(sheet)
            path=review/f'{label}-{offset//64+1:03d}.jpg'
            for i,row in enumerate(chunk):
                x,y=(i%8)*144,(i//8)*172
                with Image.open(root/row['path']) as image:sheet.paste(image,(x,y))
                draw.text((x,y+130),f'{row["person"]}/{row["session"]}',fill='black')
                draw.text((x,y+146),f'{label} cycle {row["collection_cycle"]}',fill='black')
            sheet.save(path)
            pages.append(dict(sheet=str(path.relative_to(args.output)),images=[dict(path=r['path'],sha256=r['sha256']) for r in chunk]))
    with (args.output/'review-template.json').open('x') as f:
        json.dump(dict(schema=1,status='pending',manifest_sha256=sha256(args.output/'manifest.json'),
            instructions='Inspect EVERY image and quarantine. Exclude mislabeled/partial/unclear images by path, sha256 and reason. No automatic approval.',
            excluded=[],sheets=pages),f,indent=2);f.write('\n')
    print(f'Audited {len(records)} captures; retained {len(retained)}, quarantined {len(quarantined)}. Review sheets: {review}')
    print('Labels remain PENDING REVIEW. No training, quantization, final evaluation or device access.')


def main():
    parser=argparse.ArgumentParser(description=__doc__,allow_abbrev=False)
    parser.add_argument('--root',type=Path,default=Path('.work/asl/continuous-camera'))
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--train-person',nargs='+',default=[])
    parser.add_argument('--val-person',nargs='+',default=[])
    parser.add_argument('--test-person',nargs='+',default=[])
    audit(parser.parse_args())


if __name__=='__main__':main()
