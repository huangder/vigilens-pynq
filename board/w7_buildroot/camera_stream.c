/* Opt-in, local wired-link transport. No pixels are saved to disk.
 * VLP1: magic + big-endian JSON/RGB lengths + JSON + exact PL input RGB.
 * Opt-in VLP2: magic + JSON/wire/raw lengths + JSON with codec + payload.
 * zlib-rgb-delta is zlib over same-channel left differences, reset each row;
 * raw fallback preserves exact bytes when compression cannot shrink a frame.
 * This is a diagnostic source protocol, not the frozen metric JSON schema. */
#define _POSIX_C_SOURCE 200809L
#include <arpa/inet.h>
#include <errno.h>
#include <limits.h>
#include <netinet/tcp.h>
#include <stdio.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>
#include <sys/select.h>
#include <sys/socket.h>
#include <sys/time.h>
#include <unistd.h>
#include <time.h>
#include <zlib.h>
#include "camera_stream.h"

static int client = -1;
static int listener = -1;
static int compress_rgb;
static int jpeg_mode;
static unsigned char *delta_rgb, *compressed_rgb;
static uLong compressed_capacity;
static int zlib_strategy = Z_DEFAULT_STRATEGY;
static const char *strategy_name = "default";
static unsigned sent_frames, compressed_frames;
static uint64_t raw_total, wire_total;
static double compress_seconds, send_seconds;
#define RGB_SIZE (1280u*720u*3u)

static double now_seconds(void) {
    struct timespec t;
    clock_gettime(CLOCK_MONOTONIC, &t);
    return t.tv_sec + t.tv_nsec * 1e-9;
}

int camera_stream_open(void) {
    const char *setting = getenv("VIGI_STREAM_BIND");
    char host[64], *colon, *end;
    long port;
    struct sockaddr_in address = {.sin_family = AF_INET};
    if (!setting) return 0;
    const char *codec = getenv("VIGI_STREAM_CODEC");
    if (codec && strcmp(codec, "raw") && strcmp(codec, "zlib-rgb-delta")
        && strcmp(codec, "jpeg")) return -1;
    compress_rgb = codec && !strcmp(codec, "zlib-rgb-delta");
    jpeg_mode = codec && !strcmp(codec, "jpeg");
    const char *strategy = getenv("VIGI_STREAM_ZLIB_STRATEGY");
    if (strategy && strcmp(strategy, "default") && strcmp(strategy, "rle") && strcmp(strategy, "huffman")) return -1;
    strategy_name = strategy ? strategy : "default";
    zlib_strategy = !strcmp(strategy_name, "rle") ? Z_RLE :
                    !strcmp(strategy_name, "huffman") ? Z_HUFFMAN_ONLY : Z_DEFAULT_STRATEGY;
    if (compress_rgb) {
        delta_rgb = malloc(RGB_SIZE);
        if (!delta_rgb) return -1;
    }
    if (strlen(setting) >= sizeof(host)) return -1;
    strcpy(host, setting);
    colon = strrchr(host, ':');
    if (!colon) return -1;
    *colon++ = 0;
    port = strtol(colon, &end, 10);
    if (*end || port < 1024 || port > 65535 ||
        inet_pton(AF_INET, host, &address.sin_addr) != 1 ||
        address.sin_addr.s_addr == INADDR_ANY ||
        address.sin_addr.s_addr == INADDR_BROADCAST) return -1;
    address.sin_port = htons((unsigned short)port);
    listener = socket(AF_INET, SOCK_STREAM, 0);
    if (listener < 0) return -1;
    int reuse = 1;
    if (setsockopt(listener, SOL_SOCKET, SO_REUSEADDR, &reuse, sizeof(reuse)) ||
        bind(listener, (struct sockaddr *)&address, sizeof(address)) ||
        listen(listener, 1)) return -1;
    printf("BOARD_STREAM_LISTEN bind=%s protocol=%s rgb=%s codec=%s strategy=%s\n",
           setting, (compress_rgb || jpeg_mode) ? "VLP2" : "VLP1",
           jpeg_mode ? "jpeg_decoded_on_pc" : "exact_pl_input",
           jpeg_mode ? "jpeg-mjpeg" : (compress_rgb ? "zlib-rgb-delta" : "raw"), strategy_name);
    /* The launcher waits for this marker before making its single connection. */
    if (fflush(stdout)) return -1;
    fd_set readers;
    FD_ZERO(&readers); FD_SET(listener, &readers);
    struct timeval timeout = {.tv_sec = 60};
    int ready;
    do { ready = select(listener+1, &readers, NULL, NULL, &timeout); }
    while (ready < 0 && errno == EINTR);
    if (ready <= 0) { errno = ETIMEDOUT; return -1; }
    client = accept(listener, NULL, NULL);
    if (client < 0) return -1;
    close(listener); listener = -1;
    /* Each frame has small length/JSON writes before its JPEG payload.
       Avoid Nagle/delayed-ACK stalls between those writes. */
    int no_delay = 1;
    if (setsockopt(client, IPPROTO_TCP, TCP_NODELAY, &no_delay, sizeof(no_delay))) return -1;
    timeout.tv_sec = 10; timeout.tv_usec = 0;
    if (setsockopt(client, SOL_SOCKET, SO_SNDTIMEO, &timeout, sizeof(timeout))) return -1;
    puts("BOARD_STREAM_CONNECTED pixels=local_ram_only");
    return 0;
}

static int send_all(const void *bytes, size_t size) {
    const unsigned char *next = bytes;
    while (size) {
        ssize_t sent = send(client, next, size, MSG_NOSIGNAL);
        if (sent < 0 && errno == EINTR) continue;
        if (sent <= 0) return -1;
        next += sent; size -= (size_t)sent;
    }
    return 0;
}

static int pack_delta(uLong *packed) {
    z_stream stream = {0};
    int initialized = deflateInit2(&stream, Z_BEST_SPEED, Z_DEFLATED,
                                  MAX_WBITS, 8, zlib_strategy);
    if (initialized != Z_OK) {
        fprintf(stderr, "BOARD_STREAM_ZLIB_FAIL init=%d strategy=%s\n", initialized, strategy_name);
        return -1;
    }
    /* Query this initialized strategy's bound, rather than assuming that the
     * default compressBound() also covers every non-default deflate strategy. */
    uLong bound = deflateBound(&stream, RGB_SIZE);
    if (bound > UINT_MAX || bound == 0) {
        int ended = deflateEnd(&stream);
        fprintf(stderr, "BOARD_STREAM_ZLIB_FAIL bound=%lu end=%d\n", bound, ended);
        return -1;
    }
    if (bound > compressed_capacity) {
        unsigned char *grown = realloc(compressed_rgb, bound);
        if (!grown) {
            int ended = deflateEnd(&stream);
            fprintf(stderr, "BOARD_STREAM_ZLIB_FAIL allocation=%lu end=%d\n", bound, ended);
            errno = ENOMEM;
            return -1;
        }
        compressed_rgb = grown; compressed_capacity = bound;
    }
    stream.next_in = delta_rgb; stream.avail_in = RGB_SIZE;
    stream.next_out = compressed_rgb; stream.avail_out = (uInt)bound;
    int result = deflate(&stream, Z_FINISH);
    *packed = stream.total_out;
    int complete = result == Z_STREAM_END && stream.avail_in == 0 &&
                   stream.total_in == RGB_SIZE && *packed > 0 && *packed <= bound;
    int ended = deflateEnd(&stream);
    if (!complete || ended != Z_OK) {
        fprintf(stderr, "BOARD_STREAM_ZLIB_FAIL deflate=%d end=%d complete=%d\n", result, ended, complete);
        return -1;
    }
    return 0;
}

int camera_stream_expects_jpeg(void) { return jpeg_mode; }

/* S1: forward the camera's own MJPEG bytes (~55 KB) instead of the decoded
 * 2.76 MB RGB. The PL still consumes the decoded pixels locally; the PC
 * decodes this same JPEG. Transport integrity is CRC32 over the exact bytes,
 * which is what the PC checks before decoding. */
static int send_jpeg_packet(const char *metadata, size_t metadata_size,
                            const unsigned char *jpeg, size_t jpeg_size) {
    char tagged[4096];
    if (metadata[metadata_size-1] != '}') return -1;
    unsigned long crc = crc32(0L, Z_NULL, 0);
    crc = crc32(crc, jpeg, (uInt)jpeg_size);
    int n = snprintf(tagged, sizeof(tagged),
                     "%.*s,\"codec\":\"jpeg-mjpeg\",\"jpeg_bytes\":%zu,\"jpeg_crc32\":%lu,\"jpeg_decoder\":\"%s\"}",
                     (int)metadata_size-1, metadata, jpeg_size, crc & 0xFFFFFFFFul,
                     getenv("VIGI_JPEG_FAST") ? "ifast-no-fancy" : "islow-fancy");
    if (n <= 0 || (size_t)n >= sizeof(tagged)) return -1;
    uint32_t sizes[3] = {htonl((uint32_t)n), htonl((uint32_t)jpeg_size), htonl(RGB_SIZE)};
    return (send_all("VLP2", 4) || send_all(sizes, sizeof(sizes)) ||
            send_all(tagged, (size_t)n) || send_all(jpeg, jpeg_size)) ? -1 : 0;
}

int camera_stream_send(const char *metadata, size_t metadata_size,
                       const unsigned char *rgb, size_t rgb_size,
                       const unsigned char *jpeg, size_t jpeg_size) {
    if (client < 0) return 0;
    if (!metadata_size || metadata_size > 4096 || rgb_size != RGB_SIZE) return -1;
    double started = now_seconds();
    if (jpeg_mode) {
        if (!jpeg || !jpeg_size || jpeg_size > RGB_SIZE) return -1;
        if (send_jpeg_packet(metadata, metadata_size, jpeg, jpeg_size)) return -1;
        double finished = now_seconds();
        ++sent_frames; raw_total += RGB_SIZE; wire_total += jpeg_size;
        send_seconds += finished-started;
        printf("BOARD_STREAM_FRAME n=%u codec=jpeg-mjpeg jpeg_bytes=%zu wire_bytes=%zu send_ms=%.3f\n",
               sent_frames, jpeg_size, jpeg_size, (finished-started)*1000);
        return 0;
    }
    const unsigned char *payload = rgb;
    size_t payload_size = rgb_size;
    char tagged[4096];
    const char *codec = "raw";
    if (compress_rgb) {
        /* Independent rows: first pixel unchanged, then same-channel left delta.
         * RGB fed into PL and the CPU reference remains completely untouched. */
        for (size_t row = 0; row < 720; ++row) {
            size_t base = row * 1280u * 3u;
            memcpy(delta_rgb + base, rgb + base, 3);
            for (size_t x = 3; x < 1280u * 3u; ++x)
                delta_rgb[base+x] = (unsigned char)(rgb[base+x] - rgb[base+x-3]);
        }
        uLong packed;
        if (pack_delta(&packed)) return -1;
        if (packed < rgb_size) {
            payload = compressed_rgb; payload_size = packed;
            codec = "zlib-rgb-delta"; ++compressed_frames;
        }
        if (metadata[metadata_size-1] != '}') return -1;
        int n = snprintf(tagged, sizeof(tagged), "%.*s,\"codec\":\"%s\"}",
                         (int)metadata_size-1, metadata, codec);
        if (n <= 0 || (size_t)n >= sizeof(tagged)) return -1;
        metadata = tagged; metadata_size = (size_t)n;
    }
    double compressed = now_seconds();
    if (compress_rgb) {
        uint32_t sizes[3] = {htonl((uint32_t)metadata_size), htonl((uint32_t)payload_size), htonl(RGB_SIZE)};
        if (send_all("VLP2", 4) || send_all(sizes, sizeof(sizes)) ||
            send_all(metadata, metadata_size) || send_all(payload, payload_size)) return -1;
    } else {
        uint32_t lengths[2] = {htonl((uint32_t)metadata_size), htonl((uint32_t)rgb_size)};
        if (send_all("VLP1", 4) || send_all(lengths, sizeof(lengths)) ||
            send_all(metadata, metadata_size) || send_all(rgb, rgb_size)) return -1;
    }
    double finished = now_seconds();
    ++sent_frames; raw_total += rgb_size; wire_total += payload_size;
    compress_seconds += compressed-started; send_seconds += finished-compressed;
    printf("BOARD_STREAM_FRAME n=%u codec=%s strategy=%s raw_bytes=%zu wire_bytes=%zu compress_ms=%.3f send_ms=%.3f\n",
           sent_frames, codec, strategy_name, rgb_size, payload_size, (compressed-started)*1000, (finished-compressed)*1000);
    return 0;
}

void camera_stream_close(void) {
    if (client >= 0) close(client);
    if (listener >= 0) close(listener);
    client = listener = -1;
    free(delta_rgb); free(compressed_rgb);
    delta_rgb = compressed_rgb = NULL;
    compressed_capacity = 0;
    compress_rgb = 0;
    jpeg_mode = 0;
    zlib_strategy = Z_DEFAULT_STRATEGY; strategy_name = "default";
    sent_frames = compressed_frames = 0;
    raw_total = wire_total = 0;
    compress_seconds = send_seconds = 0;
}

int camera_stream_finish(unsigned compared_frames) {
    if (client < 0) return 0;
    char metadata[64];
    int size = snprintf(metadata, sizeof(metadata), "{\"compared_frames\":%u}", compared_frames);
    if (size <= 0 || (size_t)size >= sizeof(metadata)) return -1;
    uint32_t lengths[2] = {htonl((uint32_t)size), 0};
    if (send_all("VLE1", 4) || send_all(lengths, sizeof(lengths)) ||
        send_all(metadata, (size_t)size)) return -1;
    printf("BOARD_STREAM_PASS sent_frames=%u\n", compared_frames);
    printf("BOARD_STREAM_TOTAL frames=%u compressed_frames=%u strategy=%s raw_bytes=%llu wire_bytes=%llu compress_ms=%.3f send_ms=%.3f\n",
           sent_frames, compressed_frames, strategy_name, (unsigned long long)raw_total, (unsigned long long)wire_total,
           compress_seconds*1000, send_seconds*1000);
    return 0;
}
