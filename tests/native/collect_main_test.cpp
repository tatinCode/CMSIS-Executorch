#include "preview.h"
#include "smoke_platform.h"
#include "collect_display.h"
#include <cassert>
#include <string>

int collect_main();
extern "C" { volatile SmokeStatus g_smoke_status{}; }
static std::string scenario;
static uint32_t frames,scanlines,now,exports,npu;
struct Finished {};
bool smoke_board_init(){return scenario!="board-error";}
bool preview_init(){return true;}
bool preview_frame(){
    if(exports==3){return false;}
    ++frames;++scanlines;now+=1000;return true;
}
uint32_t preview_frames(){return frames;}
uint32_t preview_scanlines(){return scanlines;}
uint32_t smoke_millis(){return now;}
bool smoke_npu_init(){++npu;return true;}
bool run_stored_image(){
    assert(frames==3);scanlines+=10;g_smoke_status.checked_values=1000;
    return scenario!="selftest-error";
}
bool camera_export_frame(){
    ++exports;assert(g_collection_sequence==exports && npu==1);
    return scenario!="export-error";
}
void smoke_set_result(bool pass){if(pass){assert(g_smoke_status.checked_values==1000);}}
void smoke_idle(){throw Finished{};}
int main(int argc,char** argv){
    assert(argc==2);scenario=argv[1];
    try{collect_main();assert(false);}catch(const Finished&){}
    if(scenario=="board-error"){assert(exports==0 && npu==0);}
    if(scenario=="selftest-error"){assert(exports==0 && g_smoke_status.error==204);}
    if(scenario=="export-error"){assert(exports==1 && g_smoke_status.error==423);}
    if(scenario=="success"){assert(exports==3 && g_smoke_status.error==208);}
}
