import argparse
import copy
import io
import json
import os
from pathlib import Path
import tempfile
import threading
import time
import unittest
import zlib

from tools import collect_camera as collect
from tools.asl_contract import STATIC_CLASSES
from ml.asl import collection_data as audit


BOOT=(b'HandGPT CONTINUOUS COLLECTION v=5; NOT ASL; LCD rotated 180\n'
      b'COEXIST SELFTEST PASS frames=5 display_events_during_test=1 elapsed_ms=19 IRQs=1 jobs=1/1 checked=1000\n'+collect.READY)


def packet(sequence=1,frame=10,fill=1):
    raw=bytes([fill])*49152;crc=zlib.crc32(raw)
    begin=f'HGCONT BEGIN 5 {sequence}\n'.encode()
    header=f'HGFRAME 2 128 128 RGB888P 49152 {frame} {crc:08x}\n'.encode()
    trailer=f'\nHGFRAME END {frame} {crc:08x}\n'.encode()
    end=f'HGCONT END 5 {sequence} {frame}\n'.encode()
    return begin+header+raw+trailer+end


class CollectProtocolTests(unittest.TestCase):
    def test_real_reader_thread_drains_and_stops_without_serial_writes(self):
        read_fd,write_fd=os.pipe();os.set_blocking(read_fd,False)
        stop=threading.Event()
        reader=collect.Collector(collect.InterruptibleStream(read_fd,time.monotonic()+5,stop),timeout=5)
        reader.start()
        try:
            os.write(write_fd,BOOT)
            self.assertTrue(reader.ready.wait(2));self.assertIsNone(reader.error)
            reader.arm('G',1)
            os.write(write_fd,packet(1,10)+packet(2,20))
            _,meta,_=reader.result()
            self.assertEqual((meta['label'],meta['collection_cycle'],meta['warmup_cycle']),('G',2,1))
            reader.arm('N',1)
            os.write(write_fd,packet(3,30)+packet(4,40))
            _,meta,_=reader.result()
            self.assertEqual((meta['label'],meta['collection_cycle'],meta['warmup_cycle']),('N',4,3))
        finally:
            stop.set();reader.thread.join(timeout=2)
            os.close(read_fd);os.close(write_fd)
        self.assertFalse(reader.thread.is_alive())

    def test_full_boot_and_cycles_preserve_pending_bytes_and_two_digit_counters(self):
        stream=audit.MemoryStream(BOOT+packet(1,10)+packet(2,20))
        self.assertEqual(collect.read_ready(stream),BOOT)
        raw,meta,log,_=collect.read_cycle(stream)
        self.assertEqual(len(raw),49152)
        self.assertEqual(meta['capture_protocol'],5)
        self.assertIn(b'HGCONT END 5 1 10',log)
        _,meta,_,_=collect.read_cycle(stream,1,10)
        self.assertEqual(meta['frame'],20)

    def test_wrong_firmware_missing_selftest_crc_end_reset_and_truncation_fail(self):
        for boot in (BOOT.replace(b'checked=1000',b'checked=7'),BOOT.replace(b'v=5',b'v=4'),
                     b'CAPTURE READY v=4;\n',b'LIVE ASL START;\n',b'COEXIST FAIL error=4\n'):
            with self.subTest(boot=boot),self.assertRaises(ValueError):collect.read_ready(audit.MemoryStream(boot))
        original=packet()
        for bad in (original.replace(b'BEGIN 5 1',b'BEGIN 5 2'),original.replace(b'END 5 1 10',b'END 5 1 11'),
                    original[:300]+b'\x02'+original[301:],original[:-5],b'HandGPT reset\n'):
            with self.subTest(bad=bad[:60]),self.assertRaises(ValueError):collect.read_cycle(audit.MemoryStream(bad))
        with self.assertRaises(ValueError):collect.read_cycle(audit.MemoryStream(packet()),0,10)

    def test_warmup_and_label_snapshot_prevent_relabeling_and_backlog_reuse(self):
        reader=collect.Collector(None);reader.ready.set();reader.boot=BOOT
        reader.arm('G',2)
        self.assertIsNone(reader.begin(10)) # discard one complete post-arm cycle
        token=reader.begin(11)
        self.assertEqual(token[1],'G')
        with self.assertRaises(ValueError):reader.arm('N',1)
        raw,meta,log,_=collect.read_cycle(audit.MemoryStream(packet(11,20)),10,0)
        reader.deliver(raw,meta,log,token)
        self.assertEqual(reader.result()[1]['label'],'G')
        token=reader.begin(12)
        raw,meta,log,_=collect.read_cycle(audit.MemoryStream(packet(12,30)),11,20)
        reader.deliver(raw,meta,log,token)
        with self.assertRaises(ValueError):reader.arm('N',1) # queued G cannot be renamed
        self.assertEqual(reader.result()[1]['label'],'G')
        reader.arm('N',1)
        self.assertIsNone(reader.begin(13))
        self.assertEqual(reader.begin(14)[1],'N')
        with self.assertRaises(ValueError):reader.deliver(raw,meta,log,token) # old generation

    def test_identifiers_and_labels_are_not_paths_commands_or_motion_letters(self):
        for bad in ('../p','p/a','', '.', 'a b'):
            with self.assertRaises(ValueError):collect.identifier(bad)
        reader=collect.Collector(None);reader.ready.set()
        for label in ('J','Z','del','space','UNKNOWN'):
            with self.assertRaises(ValueError):reader.arm(label,1)


class CollectionAuditTests(unittest.TestCase):
    def test_snapshot_ignores_new_active_sessions_but_never_missing_selected_sessions(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);self.session(root)
            active=root/'p01'/'active';active.mkdir();(active/'session.json').write_text('{}')
            with self.assertRaises(ValueError):audit.inventory(root)
            rows,sessions=audit.inventory(root,[('p01','s01')])
            self.assertEqual(len(rows),1)
            self.assertEqual(sessions[0]['session'],'s01')
            with self.assertRaises(ValueError):audit.inventory(root,[('p01','missing')])

    def test_previous_run_cycle_ids_before_reset_do_not_corrupt_current_log(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);path=self.session(root)
            log=path.with_suffix('.log');current=log.read_bytes()
            log.write_bytes(b'old binary bytes\nHGCONT BEGIN 5 2\n'+BOOT+current)
            rows,_=audit.inventory(root)
            self.assertEqual(rows[0]['collection_cycle'],2)
            log.write_bytes(current+b'\nextra packet bytes')
            with self.assertRaises(ValueError):audit.inventory(root)

    def session(self,root,person='p01',session='s01',label='G',fill=1):
        directory=root/person/session;directory.mkdir(parents=True)
        config=dict(schema=1,person=person,session=session,hand='right',labels=[label],per_label=1,connection_id='connection')
        (directory/'session.json').write_text(json.dumps(config))
        raw,meta,log,_=collect.read_cycle(audit.MemoryStream(packet(2,20,fill)),1,0)
        meta.update(label=label,warmup_cycle=1,received_at='now')
        path=collect.save_capture(directory,person,session,'right',raw,meta,BOOT+log,'connection')
        (directory/'complete.json').write_text(json.dumps(dict(status='complete',saved=[str(path.relative_to(root))],label_review_required=True)))
        return path

    def test_crc_hash_pixel_path_log_and_completion_validation(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);path=self.session(root)
            rows,sessions=audit.inventory(root)
            self.assertEqual((rows[0]['person'],rows[0]['label']),('p01','G'))
            self.assertEqual(len(sessions),1)
            meta_path=path.with_suffix('.json');original=meta_path.read_text();meta=json.loads(original)
            for key,value in (('person','p02'),('label','N'),('warmup_cycle',2),('raw_sha256','bad'),('frame',21)):
                meta_path.write_text(json.dumps({**meta,key:value}))
                with self.subTest(key=key),self.assertRaises(ValueError):audit.inventory(root)
                meta_path.write_text(original)
            raw_path=path.with_suffix('.rgb');raw_path.write_bytes(bytes(49152))
            with self.assertRaises(ValueError):audit.inventory(root)

    def test_person_split_leakage_near_duplicates_and_conflicting_labels(self):
        rows=[dict(person='p01',label='G',normalized_sha256='same',dhash=0),
              dict(person='p02',label='G',normalized_sha256='same',dhash=0),
              dict(person='p03',label='N',normalized_sha256='other',dhash=(1<<64)-1)]
        kept,quarantine=audit.split_records(rows,['p01'],['p02'],['p03'])
        self.assertEqual([r['person'] for r in kept],['p01','p03'])
        self.assertEqual(quarantine[0]['person'],'p02')
        with self.assertRaises(ValueError):audit.split_records(rows,['p01'],['p01'])
        with self.assertRaises(ValueError):audit.split_records(rows,['p01'],['p02'])
        bad=copy.deepcopy(rows);bad[1]['label']='N'
        kept,quarantine=audit.split_records(bad)
        self.assertEqual(len(quarantine),2)

    def test_audit_creates_review_sheets_but_never_auto_approves_labels(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)/'captures';self.session(root)
            output=Path(tmp)/'audit'
            args=argparse.Namespace(root=root,output=output,train_person=[],val_person=[],test_person=[])
            audit.audit(args)
            manifest=json.loads((output/'manifest.json').read_text())
            review=json.loads((output/'review-template.json').read_text())
            self.assertEqual(manifest['status'],'pending_label_review')
            self.assertEqual(manifest['counts']['unassigned']['G'],1)
            self.assertEqual(review['status'],'pending')
            self.assertTrue((output/review['sheets'][0]['sheet']).is_file())
            with self.assertRaises(ValueError):audit.audit(args)
