#ifndef VIGI_CAPTURE_BUDGET_H
#define VIGI_CAPTURE_BUDGET_H
#include <errno.h>
#include <limits.h>
#include <stdlib.h>
/* Zero is explicit continuous capture only for the board-autonomy program.
 * Probe/benchmark builds retain their bounded, positive frame budgets. */
static inline int vigi_capture_budget(const char *text,unsigned maximum,int continuous,unsigned *frames) {
    char *end;unsigned long value;
    if(!text||!frames||!*text)return -1;
    for(const char *p=text;*p;p++)if(*p<'0'||*p>'9')return -1;
    errno=0;value=strtoul(text,&end,10);
    if(errno||*end||value>UINT_MAX||value>maximum||(!value&&!continuous))return -1;
    *frames=(unsigned)value;return 0;
}
#endif
