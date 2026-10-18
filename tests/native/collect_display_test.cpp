#include "collect_display.h"
#include "camera_rotation.h"
#include <algorithm>
#include <cassert>
#include <vector>
uint32_t g_collection_sequence;
int main(){
    const size_t count=480*800;
    std::vector<uint16_t> guarded(count+2,0x1234);
    for(uint32_t n:{0u,1u,10u,UINT32_MAX}){
        std::fill(guarded.begin(),guarded.end(),0x1234);
        auto* lcd=guarded.data()+1;
        assert(handgpt::draw_collection_panel(lcd,count,n));
        assert(guarded.front()==0x1234 && guarded.back()==0x1234);
        assert(std::all_of(lcd,lcd+480*480,[](uint16_t v){return v==0x1234;}));
        assert(std::count(lcd+480*500,lcd+480*530,0xffe0)>0);
        assert(std::count(lcd+480*580,lcd+480*622,0xffe0)>0);
        auto before=guarded;
        assert(handgpt::rotate_lcd180(lcd,count));
        assert(handgpt::rotate_lcd180(lcd,count));
        assert(before==guarded);
    }
    assert(!handgpt::draw_collection_panel(nullptr,count,1));
    assert(!handgpt::draw_collection_panel(guarded.data()+1,count-1,1));
}
