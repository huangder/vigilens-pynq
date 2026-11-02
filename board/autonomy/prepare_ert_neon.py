"""Patch an explicitly supplied local dlib 19.24.6 evaluation copy only.

The upstream Boost-licensed header and model stay outside the repository.
Tree order and per-coordinate float addition order are retained; the SIMD
candidate must still be compared with the original binary on the target.
"""
import argparse
import hashlib
import json
from pathlib import Path

def prepare(source:Path):
    header=source/'dlib/image_processing/shape_predictor.h'
    text=header.read_text(encoding='utf-8')
    marker='VIGILENS_ERT_NEON_EVALUATION'
    if marker in text:raise ValueError('evaluation source already patched')
    before=hashlib.sha256(header.read_bytes()).hexdigest()
    anchor='''                for (unsigned long i = 0; i < forests[iter].size(); ++i)
                    current_shape += forests[iter][i](feature_pixel_values, leaf_idx);'''
    if text.count(anchor)!=1:raise ValueError('pinned dlib prediction loop changed')
    replacement='''                // VIGILENS_ERT_NEON_EVALUATION: preserve tree and addition order.
                for (unsigned long i = 0; i < forests[iter].size(); ++i)
                {
                    const auto& update = forests[iter][i](feature_pixel_values, leaf_idx);
#if defined(VIGI_ERT_NEON) && defined(__ARM_NEON)
                    long k=0;
                    float *dst=&current_shape(0);
                    const float *src=&update(0);
                    for (; k+4<=current_shape.size(); k+=4)
                        vst1q_f32(dst+k,vaddq_f32(vld1q_f32(dst+k),vld1q_f32(src+k)));
                    for (; k<current_shape.size(); ++k) dst[k]+=src[k];
#else
                    current_shape += update;
#endif
                }'''
    text=text.replace(anchor,replacement)
    include='#include <utility>'
    if text.count(include)!=1:raise ValueError('pinned dlib include changed')
    text=text.replace(include,include+'\n#if defined(VIGI_ERT_NEON) && defined(__ARM_NEON)\n#include <arm_neon.h>\n#endif')
    header.write_text(text,encoding='utf-8',newline='\n')
    result={'source_before_sha256':before,'source_after_sha256':hashlib.sha256(header.read_bytes()).hexdigest(),
            'scope':'local evaluation header only','upstream_license':'Boost Software License',
            'target_parity_verified':False}
    (source.parent/'neon_source_patch.json').write_text(json.dumps(result,indent=2)+'\n',encoding='utf-8')
    return result

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source',type=Path)
    print(json.dumps(prepare(parser.parse_args().source)))
