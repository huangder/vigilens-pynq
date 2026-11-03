/* Read-only HTTP adapter over autonomy_camera's atomically renamed latest file.
 * Optional display-only JPEG file; no capture, inference or shell calls.
 * Build via build_native_vision.sh (exports canonical HTTP envelope header).
 * All CLI options below are required; stale-seconds comes from config.yaml's
 * existing ws_disconnect_timeout_s. File mtime measures publication freshness;
 * contract ts may use a different (capture/monotonic) clock and is not rewritten.
 */
#define _POSIX_C_SOURCE 200809L
#include <arpa/inet.h>
#include <ctype.h>
#include <errno.h>
#include <fcntl.h>
#include <limits.h>
#include <math.h>
#include <poll.h>
#include <signal.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/socket.h>
#include <sys/stat.h>
#include <time.h>
#include <unistd.h>
#include "vg_http_envelope.h"

/* Transport/storage bounds, not measurement thresholds. */
enum { HEADER_CAP=8192, BODY_CAP=262144, FRAME_CAP=4194304, IO_TIMEOUT_MS=2000, JSON_DEPTH=16 };
static volatile sig_atomic_t stopping;
static void stop(int sig) { (void)sig; stopping=1; }
static double clock_seconds(clockid_t clock_id) {
    struct timespec t;
    if (clock_gettime(clock_id,&t)) return -1;
    return (double)t.tv_sec+(double)t.tv_nsec/1e9;
}
static int ready(int fd, short events, double deadline) {
    struct pollfd p={fd,events,0};
    for (;;) {
        double remaining=(deadline-clock_seconds(CLOCK_MONOTONIC))*1000;
        int rc;
        if (stopping || remaining<=0) return -1;
        rc=poll(&p,1,(int)ceil(remaining));
        if (rc<0 && errno==EINTR) continue;
        if (rc<=0 || (p.revents&(POLLERR|POLLNVAL))) return -1;
        return (p.revents&events) ? 0 : -1;
    }
}
static int send_all(int fd, const char *data, size_t n, double deadline) {
    while (n) {
        ssize_t sent;
        if (ready(fd,POLLOUT,deadline)) return -1;
        sent=send(fd,data,n,MSG_NOSIGNAL);
        if (sent<0 && (errno==EINTR || errno==EAGAIN || errno==EWOULDBLOCK)) continue;
        if (sent<=0) return -1;
        data+=sent; n-=(size_t)sent;
    }
    return 0;
}
static void response(int fd, int code, const char *reason, const char *type,
                     const char *body, size_t length) {
    char header[512];
    double deadline=clock_seconds(CLOCK_MONOTONIC)+IO_TIMEOUT_MS/1000.0;
    int n=snprintf(header,sizeof(header),
        "HTTP/1.1 %d %s\r\nContent-Type: %s\r\nContent-Length: %zu\r\n"
        "Connection: close\r\nCache-Control: no-store\r\n"
        "X-Content-Type-Options: nosniff\r\n\r\n",code,reason,type,length);
    if (n>0 && (size_t)n<sizeof(header) && !send_all(fd,header,(size_t)n,deadline))
        (void)send_all(fd,body,length,deadline);
}
static void error_reply(int fd,int code,const char *reason,const char *error) {
    char body[192];
    int n=snprintf(body,sizeof(body),"{\"error\":\"%s\"}\n",error);
    if (n>0 && (size_t)n<sizeof(body)) response(fd,code,reason,"application/json; charset=utf-8",body,(size_t)n);
}

/* Validate complete JSON syntax and the nine-field envelope. Measurement/schema
 * semantics remain owned by the existing contract writer, not this transport.
 * No partial object, duplicate/missing top-level key or trailing data is served. */
typedef struct { const char *p,*end; } json;
static void space(json *j) {
    while (j->p<j->end && (*j->p==' ' || *j->p=='\t' || *j->p=='\r' || *j->p=='\n')) ++j->p;
}
static int string(json *j, const char **start, size_t *length) {
    const char *begin;
    if (j->p==j->end || *j->p++!='"') return -1;
    begin=j->p;
    while (j->p<j->end) {
        unsigned char c=(unsigned char)*j->p++;
        if (c=='"') { if(start)*start=begin; if(length)*length=(size_t)(j->p-begin-1); return 0; }
        if (c<0x20) return -1;
        if (c=='\\') {
            int k;
            if (j->p==j->end) return -1;
            c=(unsigned char)*j->p++;
            if (c=='u') {
                for(k=0;k<4;++k) { if(j->p==j->end || !isxdigit((unsigned char)*j->p))return -1; ++j->p; }
            } else if (!c || !strchr("\"\\/bfnrt",c)) return -1;
        }
    }
    return -1;
}
static int value(json *j,int depth);
static int number(json *j) {
    const char *begin=j->p;
    char *end;
    double v;
    if(j->p<j->end && *j->p=='-')++j->p;
    if(j->p==j->end)return -1;
    if(*j->p=='0')++j->p;
    else {
        if(*j->p<'1'||*j->p>'9')return -1;
        do {++j->p;} while(j->p<j->end && isdigit((unsigned char)*j->p));
    }
    if(j->p<j->end && *j->p=='.') {
        ++j->p;
        if(j->p==j->end || !isdigit((unsigned char)*j->p))return -1;
        do {++j->p;} while(j->p<j->end && isdigit((unsigned char)*j->p));
    }
    if(j->p<j->end && (*j->p=='e'||*j->p=='E')) {
        ++j->p;
        if(j->p<j->end && (*j->p=='+'||*j->p=='-'))++j->p;
        if(j->p==j->end || !isdigit((unsigned char)*j->p))return -1;
        do {++j->p;} while(j->p<j->end && isdigit((unsigned char)*j->p));
    }
    errno=0; v=strtod(begin,&end);
    return end!=j->p || !isfinite(v) ? -1 : 0;
}
static int container(json *j,int depth,int object) {
    char close=object ? '}' : ']';
    ++j->p; space(j);
    if(j->p<j->end && *j->p==close) {++j->p;return 0;}
    for(;;) {
        if(object) {
            if(string(j,NULL,NULL))return -1;
            space(j); if(j->p==j->end || *j->p++!=':')return -1;
        }
        if(value(j,depth+1))return -1;
        space(j); if(j->p==j->end)return -1;
        if(*j->p==close){++j->p;return 0;}
        if(*j->p++!=',')return -1;
        space(j);
    }
}
static int value(json *j,int depth) {
    const char *word=NULL;
    size_t n;
    if(depth>JSON_DEPTH)return -1;
    space(j); if(j->p==j->end)return -1;
    switch(*j->p) {
    case '"':return string(j,NULL,NULL);
    case '{':return container(j,depth,1);
    case '[':return container(j,depth,0);
    case 't':word="true";break;
    case 'f':word="false";break;
    case 'n':word="null";break;
    default:return number(j);
    }
    n=strlen(word);
    if((size_t)(j->end-j->p)<n || memcmp(j->p,word,n))return -1;
    j->p+=n;return 0;
}
static int contract_envelope(const char *body,size_t n) {
    static const char *keys[]=VG_HTTP_KEYS;
    json j={body,body+n};
    unsigned seen=0;
    space(&j); if(j.p==j.end || *j.p++!='{')return -1;
    for(;;) {
        const char *key;
        size_t len;
        unsigned k;
        space(&j); if(string(&j,&key,&len))return -1;
        for(k=0;k<VG_HTTP_KEY_COUNT;++k)if(strlen(keys[k])==len && !memcmp(keys[k],key,len))break;
        if(k==VG_HTTP_KEY_COUNT || (seen&(1u<<k)))return -1;
        seen|=1u<<k;
        space(&j); if(j.p==j.end || *j.p++!=':')return -1;
        space(&j); if(j.p==j.end)return -1;
        if(k<2) { if(*j.p<'0'||*j.p>'9')return -1; }
        else if(k<6) { if(*j.p!='{')return -1; }
        else { if(*j.p!='"')return -1; }
        if(value(&j,0))return -1;
        space(&j); if(j.p==j.end)return -1;
        if(*j.p=='}'){++j.p;break;}
        if(*j.p++!=',')return -1;
    }
    space(&j); return seen==((1u<<VG_HTTP_KEY_COUNT)-1) && j.p==j.end ? 0 : -1;
}
static int read_file(const char *path,char *body,size_t capacity,size_t *length,double stale) {
    struct stat st;
    size_t used=0;
    int fd=open(path,O_RDONLY|O_NONBLOCK|O_CLOEXEC);
    if(fd<0)return -1;
    if(fstat(fd,&st) || !S_ISREG(st.st_mode) || st.st_size<=0 || (uint64_t)st.st_size>=capacity) {close(fd);return -1;}
    if(stale>0) {
        double age=clock_seconds(CLOCK_REALTIME)-((double)st.st_mtim.tv_sec+st.st_mtim.tv_nsec/1e9);
        if(age<0 || age>stale){close(fd);return -2;}
    }
    while(used<(size_t)st.st_size) {
        ssize_t got=read(fd,body+used,(size_t)st.st_size-used);
        if(got<0 && errno==EINTR)continue;
        if(got<=0){close(fd);return -1;}
        used+=(size_t)got;
    }
    close(fd); body[used]=0; *length=used;
    return 0;
}
static const char *asset_type(const char *path) {
    const char *suffix=strrchr(path,'.');
    if(path[0]!='/' || strstr(path,"..") || !suffix)return NULL;
    for(const char *p=path;*p;++p)
        if(!isalnum((unsigned char)*p) && *p!='/' && *p!='_' && *p!='-' && *p!='.')return NULL;
    if(!strcmp(suffix,".js"))return "text/javascript; charset=utf-8";
    if(!strcmp(suffix,".css"))return "text/css; charset=utf-8";
    if(!strncmp(path,"/icons/",7) && !strcmp(suffix,".svg"))return "image/svg+xml";
    return NULL;
}
static void serve(int fd,const char *latest,const char *display,const char *preview,const char *assets,double stale) {
    char header[HEADER_CAP],method[16],path[512],version[16],extra;
    char *body=NULL,*line_end;
    size_t used=0,length=0;
    int image_request;
    const char *static_type;
    double deadline=clock_seconds(CLOCK_MONOTONIC)+IO_TIMEOUT_MS/1000.0;
    for(;;) {
        ssize_t got;
        if(used==sizeof(header)-1){error_reply(fd,431,"Request Header Fields Too Large","headers_too_large");return;}
        if(ready(fd,POLLIN,deadline)){error_reply(fd,408,"Request Timeout","request_timeout");return;}
        got=recv(fd,header+used,sizeof(header)-1-used,0);
        if(got<0 && (errno==EINTR||errno==EAGAIN||errno==EWOULDBLOCK))continue;
        if(got<=0)return;
        if(memchr(header+used,0,(size_t)got)){error_reply(fd,400,"Bad Request","invalid_request");return;}
        used+=(size_t)got; header[used]=0;
        if(strstr(header,"\r\n\r\n"))break;
    }
    line_end=strstr(header,"\r\n");
    if(!line_end){error_reply(fd,400,"Bad Request","invalid_request");return;}
    *line_end=0;
    if(sscanf(header,"%15s %511s %15s %c",method,path,version,&extra)!=3 ||
        (strcmp(version,"HTTP/1.0") && strcmp(version,"HTTP/1.1"))) {
        error_reply(fd,400,"Bad Request","invalid_request");return;
    }
    if(strcmp(method,"GET")){error_reply(fd,405,"Method Not Allowed","get_only");return;}
    image_request=!strcmp(path,"/api/frame.jpg") || !strncmp(path,"/api/frame.jpg?",15);
    static_type=assets ? asset_type(path) : NULL;
    if(strcmp(path,"/") && strcmp(path,"/api/latest") && !image_request && !static_type){error_reply(fd,404,"Not Found","not_found");return;}
    body=malloc(image_request ? FRAME_CAP : BODY_CAP);
    if(!body){error_reply(fd,503,"Service Unavailable","out_of_memory");return;}
    if(static_type) {
        char filename[PATH_MAX];
        int n=snprintf(filename,sizeof(filename),"%s%s",assets,path);
        if(n<0 || (size_t)n>=sizeof(filename) || read_file(filename,body,BODY_CAP,&length,0))error_reply(fd,404,"Not Found","not_found");
        else response(fd,200,"OK",static_type,body,length);
    } else if(image_request) {
        int rc=preview ? read_file(preview,body,FRAME_CAP,&length,stale) : -1;
        if(rc || length<4 || (unsigned char)body[0]!=0xff || (unsigned char)body[1]!=0xd8 ||
                (unsigned char)body[length-2]!=0xff || (unsigned char)body[length-1]!=0xd9)
            error_reply(fd,503,"Service Unavailable",rc==-2 ? "preview_stale" : "preview_unavailable");
        else response(fd,200,"OK","image/jpeg",body,length);
    } else if(!strcmp(path,"/api/latest")) {
        int rc=read_file(latest,body,BODY_CAP,&length,stale);
        if(rc || contract_envelope(body,length))error_reply(fd,503,"Service Unavailable",rc==-2 ? "measurement_stale" : "measurement_unavailable");
        else response(fd,200,"OK","application/json; charset=utf-8",body,length);
    } else {
        if(read_file(display,body,BODY_CAP,&length,0))error_reply(fd,503,"Service Unavailable","display_unavailable");
        else response(fd,200,"OK","text/html; charset=utf-8",body,length);
    }
    free(body);
}
int main(int argc,char **argv) {
    const char *bind_address=NULL,*latest=NULL,*display=NULL,*preview=NULL,*assets=NULL;
    double stale=0;
    long port=0;
    int i,listener,one=1,rc=0;
    struct sockaddr_in address;
    for(i=1;i<argc;++i) {
        const char *option=argv[i],*argument;
        char *end;
        if(i+1==argc)goto usage;
        argument=argv[++i];
        if(!strcmp(option,"--bind"))bind_address=argument;
        else if(!strcmp(option,"--latest"))latest=argument;
        else if(!strcmp(option,"--display"))display=argument;
        else if(!strcmp(option,"--preview"))preview=argument;
        else if(!strcmp(option,"--assets"))assets=argument;
        else if(!strcmp(option,"--port")) {
            errno=0;port=strtol(argument,&end,10);
            if(errno || end==argument || *end || port<1 || port>65535)goto usage;
        } else if(!strcmp(option,"--stale-seconds")) {
            stale=strtod(argument,&end);
            if(end==argument || *end || !isfinite(stale) || stale<=0)goto usage;
        } else goto usage;
    }
    memset(&address,0,sizeof(address));address.sin_family=AF_INET;
    if(!bind_address || !latest || !display || !*latest || !*display || (preview && !*preview) || (assets && !*assets) || !port || stale<=0 ||
        inet_pton(AF_INET,bind_address,&address.sin_addr)!=1)goto usage;
    address.sin_port=htons((uint16_t)port);
    signal(SIGPIPE,SIG_IGN);
    { struct sigaction sa; memset(&sa,0,sizeof(sa));sa.sa_handler=stop;sigemptyset(&sa.sa_mask);
      sigaction(SIGINT,&sa,NULL);sigaction(SIGTERM,&sa,NULL); }
    listener=socket(AF_INET,SOCK_STREAM,0);
    if(listener<0){perror("socket");return 1;}
    setsockopt(listener,SOL_SOCKET,SO_REUSEADDR,&one,sizeof(one));
    if(bind(listener,(struct sockaddr *)&address,sizeof(address)) || listen(listener,8)) {perror("bind/listen");close(listener);return 1;}
    printf("METRICS_HTTP_READY bind=%s port=%ld latest=%s stale_seconds=%.17g read_only=1\n",bind_address,port,latest,stale);fflush(stdout);
    while(!stopping) {
        int client=accept(listener,NULL,NULL),flags;
        if(client<0){if(errno==EINTR)continue;perror("accept");rc=1;break;}
        flags=fcntl(client,F_GETFL,0);
        if(flags>=0 && !fcntl(client,F_SETFL,flags|O_NONBLOCK))serve(client,latest,display,preview,assets,stale);
        close(client);
    }
    close(listener);return rc;
usage:
    fprintf(stderr,"usage: %s --bind IPv4 --port PORT --latest PATH --display PATH [--preview PATH] [--assets DIR] --stale-seconds SECONDS\n",argv[0]);return 2;
}
