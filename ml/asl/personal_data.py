"""Merge reviewed pilot TRAIN with preserved camera TRAIN/VAL. Never moves VAL into TRAIN."""

import argparse
import json
from pathlib import Path

from tools import inference as common
from tools.asl_contract import STATIC_CLASSES, STATIC_LETTERS
from .adapt import load_camera_data
from .camera_data import write_json
from .collection_data import inventory, split_records
from .data import sha256


def screen_new(training, validation, candidates):
    """Preserve old validation exactly; quarantine conflicting/leaking NEW rows."""
    accepted,quarantined=[],[]
    by_pixels={}
    for r in training+validation+candidates:
        by_pixels.setdefault(r['normalized_sha256'],set()).add(r['label'])
    seen={r['normalized_sha256'] for r in training}
    for r in candidates:
        matches=[v['path'] for v in validation if v['normalized_sha256']==r['normalized_sha256'] or
                 (v['dhash']^r['dhash']).bit_count()<=2]
        reason=('identical_pixels_conflicting_labels' if len(by_pixels[r['normalized_sha256']])>1 else
                'matches_preserved_legacy_validation' if matches else
                'exact_duplicate_training' if r['normalized_sha256'] in seen else None)
        if reason:quarantined.append(dict(r,reason=reason,matches=matches))
        else:accepted.append(r);seen.add(r['normalized_sha256'])
    return accepted,quarantined


def merge(legacy_manifest,legacy_review,collection_manifest,label_review):
    old_root,old,_,_=load_camera_data(Path(legacy_manifest),Path(legacy_review))
    audit=json.loads(Path(collection_manifest).read_text())
    if (audit.get('schema')!=1 or audit.get('kind')!='continuous_static_camera_pending_review' or
            audit.get('classes')!=list(STATIC_CLASSES) or
            any(audit.get('person_assignments',{}).get(s) for s in ('train','val','test'))):
        raise ValueError('Use the unassigned collection audit; pilot sessions are TRAIN only, never a new test')
    root=Path(audit['root']).resolve(strict=True)
    current,sessions=inventory(root,[(s['person'],s['session']) for s in audit['sessions']])
    if {r['person'] for r in current}!={'p01'}:
        raise ValueError('This solo-presenter merge requires p01 only; do not disguise additional people as p01')
    retained,quarantined=split_records(current)
    if retained!=audit['records'] or quarantined!=audit['quarantined'] or sessions!=audit['sessions']:
        raise ValueError('Collection data or sidecars changed since audit')
    review=json.loads(Path(label_review).read_text())
    if (review.get('schema')!=1 or review.get('status') not in ('reviewed','reviewed_with_exclusions') or
            review.get('collection_manifest_sha256')!=sha256(collection_manifest)):
        raise ValueError('A matching completed visual label review is required')
    by_path={r['path']:r for r in current};excluded=review.get('excluded',[])
    if len({r['path'] for r in excluded})!=len(excluded):raise ValueError('Repeated label exclusion')
    for row in excluded:
        if row['path'] not in by_path or by_path[row['path']]['sha256']!=row['sha256'] or not row.get('reason'):
            raise ValueError('Label exclusion does not match its original image')
    removed={r['path'] for r in excluded}

    def mapped(row,base,origin):
        absolute=(base/row['path']).resolve(strict=True)
        if not absolute.is_relative_to(common.ROOT):raise ValueError('Camera inputs must remain inside the repository workspace')
        if sha256(absolute)!=row['sha256']:raise ValueError('Reviewed camera image changed')
        return dict(row,path=str(absolute.relative_to(common.ROOT)),original_path=row['path'],origin=origin,
                    person=row.get('person','p01'),session=origin+':'+row['session'])

    old=[mapped(r,old_root,'legacy') for r in old]
    training=[r for r in old if r['split']=='train'];validation=[r for r in old if r['split']=='val']
    candidates=[dict(mapped(r,root,'continuous_pilot'),split='train') for r in retained if r['path'] not in removed]
    accepted,leaks=screen_new(training,validation,candidates)
    if not accepted:raise ValueError('No usable new training images after review/leakage checks')
    records=training+accepted+validation
    return records,dict(label_exclusions=excluded,collection_quarantine=quarantined,new_training_quarantine=leaks,
                        old_training_count=len(training),old_validation_count=len(validation),new_training_count=len(accepted),
                        same_presenter='User identifies old/new captures as their own; no independent identity verification',
                        new_camera_validation=False)


def load_personal_data(manifest_path,review_path):
    manifest=json.loads(Path(manifest_path).read_text())
    if (manifest.get('schema')!=1 or manifest.get('kind')!='personal_static_camera_development' or
            manifest.get('classes')!=list(STATIC_CLASSES) or manifest.get('final_test_present') is not False or
            manifest.get('root')!=str(common.ROOT) or manifest.get('label_review')!=str(Path(review_path).resolve())):
        raise ValueError('Expected the explicit personal TRAIN + preserved VAL manifest')
    for path,digest in manifest['input_sha256'].items():
        if sha256(path)!=digest:raise ValueError('Personal camera dependency changed: '+path)
    records,screening=merge(*(Path(manifest[k]) for k in ('legacy_manifest','legacy_review','collection_manifest','label_review')))
    if records!=manifest['records'] or screening!=manifest['screening']:
        raise ValueError('Personal camera inventory/review/split changed')
    return common.ROOT,records,manifest,json.loads(Path(review_path).read_text())


def prepare(args):
    if args.output.exists():raise ValueError('Choose a new personal manifest directory')
    paths={k:Path(getattr(args,k)).resolve(strict=True) for k in ('legacy_manifest','legacy_review','collection_manifest','label_review')}
    records,screening=merge(*paths.values())
    counts={s:{c:sum(r['split']==s and r['label']==c for r in records) for c in STATIC_CLASSES} for s in ('train','val')}
    inputs=(*paths.values(),Path(__file__),Path(__file__).with_name('collection_data.py'),
            common.ROOT/'tools/collect_camera.py',common.ROOT/'tools/asl_contract.py')
    manifest=dict(schema=1,kind='personal_static_camera_development',root=str(common.ROOT),classes=list(STATIC_CLASSES),
        **{k:str(p) for k,p in paths.items()},input_sha256={str(p):sha256(p) for p in inputs},
        records=records,screening=screening,counts=counts,final_test_present=False,
        camera_untrained_letters=[c for c in STATIC_LETTERS if not counts['train'][c]],
        camera_unvalidated_letters=[c for c in STATIC_LETTERS if not counts['val'][c]],
        limitations=['All new usable pilot captures are TRAIN only, not new-letter validation.',
                     'Preserved legacy validation has already influenced prior models; development evidence only.',
                     'One presenter, limited backgrounds; not unseen-person or final camera accuracy.'])
    args.output.mkdir(parents=True)
    write_json(args.output/'manifest.json',manifest)
    load_personal_data(args.output/'manifest.json',paths['label_review'])
    print(json.dumps(dict(counts=counts,screening=screening,camera_unvalidated_letters=manifest['camera_unvalidated_letters']),indent=2))


def main():
    parser=argparse.ArgumentParser(description=__doc__,allow_abbrev=False)
    parser.add_argument('--legacy-manifest',type=Path,default=Path('.work/asl/camera-audit-v1/manifest.json'))
    parser.add_argument('--legacy-review',type=Path,default=Path('.work/asl/camera-audit-v1/label-review.json'))
    parser.add_argument('--collection-manifest',type=Path,default=Path('.work/asl/personal-review-v1/manifest.json'))
    parser.add_argument('--label-review',type=Path,default=Path('.work/asl/personal-review-v1/label-review.json'))
    parser.add_argument('--output',type=Path,default=Path('.work/asl/personal-camera-v3'))
    prepare(parser.parse_args())


if __name__=='__main__':main()
