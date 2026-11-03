/* Read complete libjpeg output groups directly into the owned RGB buffer.
 * rec_outbuf_height is the library's recommendation, not a measurement rate.
 * The caller must have validated RGB dimensions and allocated height*stride.
 */
#ifndef VG_JPEG_ROWS_H
#define VG_JPEG_ROWS_H
#include <stddef.h>
#include <stdio.h>
#include <jpeglib.h>
static int vg_jpeg_read_rgb_rows(j_decompress_ptr decoder,unsigned char *rgb,size_t stride) {
    JSAMPROW rows[MAX_SAMP_FACTOR];
    if(!decoder||!rgb||decoder->output_components!=3||
       stride<(size_t)decoder->output_width*3||decoder->rec_outbuf_height<1||
       decoder->rec_outbuf_height>MAX_SAMP_FACTOR)return -1;
    while(decoder->output_scanline<decoder->output_height) {
        JDIMENSION count=decoder->output_height-decoder->output_scanline;
        if(count>(JDIMENSION)decoder->rec_outbuf_height)count=(JDIMENSION)decoder->rec_outbuf_height;
        for(JDIMENSION i=0;i<count;++i)rows[i]=rgb+((size_t)decoder->output_scanline+i)*stride;
        JDIMENSION read=jpeg_read_scanlines(decoder,rows,count);
        if(!read||read>count)return -1;
    }
    return 0;
}
#endif
