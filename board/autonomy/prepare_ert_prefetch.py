"""Extend an explicitly supplied ORIGINAL dlib v19.24.6 evaluation copy.

Keeps the Boost-licensed upstream header and original operator intact. The new
predict_batched method computes tree leaves before summing them in the original
tree/coordinate order. Prefetch changes memory scheduling, not tree selection.
Source and third-party model remain outside the repository.
"""
import argparse
import hashlib
import json
from pathlib import Path

ORIGINAL_SHA256='bc26beef755bfdbb45b18b4a91046111aecf78e12b5fd6e734e47a7f8a387128'

def prepare(source:Path):
    header=source/'dlib/image_processing/shape_predictor.h'
    raw=header.read_bytes()
    if hashlib.sha256(raw).hexdigest()!=ORIGINAL_SHA256:
        raise ValueError('requires original pinned dlib v19.24.6 header')
    text=raw.decode('utf-8')
    start=text.index('        template <typename image_type>\n        full_object_detection operator()(')
    end=text.index('        template <typename image_type, typename T, typename U>',start)
    original=text[start:end]
    batched=original.replace('full_object_detection operator()(', 'full_object_detection predict_batched(',1)
    batched=batched.replace('const rectangle& rect\n','const rectangle& rect,\n            unsigned int prefetch_ahead\n',1)
    batched=batched.replace('std::vector<float> feature_pixel_values;',
        'std::vector<float> feature_pixel_values;\n            std::vector<const matrix<float,0,1>*> updates;',1)
    anchor='''                for (unsigned long i = 0; i < forests[iter].size(); ++i)
                    current_shape += forests[iter][i](feature_pixel_values, leaf_idx);'''
    if batched.count(anchor)!=1:raise ValueError('prediction loop changed')
    replacement='''                // VIGILENS_ERT_PREFETCH_EVALUATION: feature values are fixed
                // within this cascade stage. Tree leaves do not depend on the
                // intermediate sum. Preserve float addition order below.
                updates.resize(forests[iter].size());
                for (unsigned long i = 0; i < forests[iter].size(); ++i)
                    updates[i] = &forests[iter][i](feature_pixel_values, leaf_idx);
                for (unsigned long i = 0; i < updates.size(); ++i)
                {
#if defined(__GNUC__)
                    const unsigned long future = i + prefetch_ahead;
                    if (prefetch_ahead && future < updates.size())
                    {
                        const float *src = &(*updates[future])(0);
                        // Cortex-A9 has 32-byte cache lines; cover the leaf.
                        for (long k = 0; k < current_shape.size(); k += 8)
                            __builtin_prefetch(src + k, 0, 1);
                    }
#endif
                    current_shape += *updates[i];
                }'''
    batched=batched.replace(anchor,replacement)
    text=text[:end]+batched+text[end:]
    header.write_text(text,encoding='utf-8',newline='\n')
    result={'source_before_sha256':ORIGINAL_SHA256,
        'source_after_sha256':hashlib.sha256(header.read_bytes()).hexdigest(),
        'original_operator_unchanged':True,'upstream_license':'Boost Software License',
        'target_parity_verified':False}
    (source.parent/'prefetch_source_patch.json').write_text(json.dumps(result,indent=2)+'\n',encoding='utf-8')
    return result

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source',type=Path)
    print(json.dumps(prepare(parser.parse_args().source)))
