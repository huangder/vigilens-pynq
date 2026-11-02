#include "capture_budget.h"
#include <assert.h>
#include <stdio.h>
int main(void) {
    unsigned n=123;
    assert(vigi_capture_budget("0",36000,0,&n)!=0&&n==123);
    assert(vigi_capture_budget("0",36000,1,&n)==0&&n==0);
    assert(vigi_capture_budget("36000",36000,1,&n)==0&&n==36000);
    assert(vigi_capture_budget("36001",36000,1,&n)!=0);
    assert(vigi_capture_budget("-1",36000,1,&n)!=0);
    assert(vigi_capture_budget("1x",36000,1,&n)!=0);
    assert(vigi_capture_budget("",36000,1,&n)!=0);
    assert(vigi_capture_budget("999999999999999999999999",36000,1,&n)!=0);
    assert(vigi_capture_budget("300",300,0,&n)==0&&n==300);
    puts("CAPTURE_BUDGET_PASS: continuous opt-in, bounded probes, strict parsing");return 0;
}
