MEMORY
{
  ITCM (rwx) : ORIGIN = 0x00000000 , LENGTH = 0x00040000
  DTCM (rwx) : ORIGIN = 0x20000000 , LENGTH = 0x00100000
  SRAM0 (rwx) : ORIGIN = 0x02000000, LENGTH = 0x00400000
  SRAM1 (rwx) : ORIGIN = 0x02400000, LENGTH = 0x00400000
  MRAM (rx) : ORIGIN = 0x80001000, LENGTH = (0x80500000 - 0x80001000)
  MRAM_USER (rw) : ORIGIN = 0x80500000, LENGTH = 0x00080000
}
ENTRY(Reset_Handler)
SECTIONS
{
  /DISCARD/ :
  {
      *(.ARM.exidx*)
      *(.ARM.extab* .gnu.linkonce.armextab.*)
  }
  .startup.at_mram : ALIGN(2048)
  {
    KEEP(*(.vectors))
    *startup*(.text .rodata*)
    *system*(.text .rodata*)
    *sys_*(.text .rodata*)
    *pm*(.text .rodata*)
    *tgu*(.text .rodata*)
    *mpu*(.text .rodata*)
    *sau_tcm_ns_setup*(.text .rodata*)
    *lv_*(.text .rodata*)
    . = ALIGN(16);
  } > MRAM
  .copy.table : ALIGN(4)
  {
    __copy_table_start__ = .;
    LONG ( LOADADDR(.data.at_dtcm) )
    LONG ( ADDR(.data.at_dtcm) )
    LONG ( SIZEOF(.data.at_dtcm)/4 )
    LONG ( LOADADDR(.code.at_itcm) )
    LONG ( ADDR(.code.at_itcm) )
    LONG ( SIZEOF(.code.at_itcm)/4 )
    __copy_table_end__ = .;
    . = ALIGN(16);
  } > MRAM
  .zero.table : ALIGN(4)
  {
    __zero_table_start__ = .;
    LONG (ADDR(.bss))
    LONG (SIZEOF(.bss)/4)
    LONG (ADDR(.bss.at_sram0))
    LONG (SIZEOF(.bss.at_sram0)/4)
    __zero_table_end__ = .;
    . = ALIGN(16);
  } > MRAM
  .got : ALIGN(8)
  {
    *(.got)
    *(.got*)
    *(.data.rel.ro)
    *(.data.rel.ro*)
    *(.got.plt)
    . = ALIGN(16);
  } > MRAM
  .tdata.at_mram : ALIGN(16)
  {
    __tls_base = .;
    __arm32_tls_tcb_offset = .;
    *(.tdata .tdata.* .gnu.linkonce.td.*)
    __tdata_end = .;
    . = ALIGN(16);
  } > MRAM
  .tbss (NOLOAD) : ALIGN(16)
  {
    *(.tbss .tbss.* .gnu.linkonce.tb.*)
    *(.tcommon)
    __tbss_end = .;
    . = ALIGN(16);
  } > MRAM
  .code.at_itcm : ALIGN(8)
  {
    *(.text.handgpt_itcm*)
    . = ALIGN(16);
  } > ITCM AT > MRAM
  .data.at_dtcm : ALIGN(8)
  {
    *(vtable)
    *(.data)
    *(.data*)
    *arm_common_tables*(.data* .rodata*)
    KEEP(*(.jcr*))
    . = ALIGN(8);
    *(.ARM.extab* .gnu.linkonce.armextab.*)
    . = ALIGN(8);
    __exidx_start = .;
    *(.ARM.exidx* .gnu.linkonce.armexidx.*)
    __exidx_end = .;
    . = ALIGN(16);
  } > DTCM AT > MRAM
  .bss.at_sram0 (NOLOAD) : ALIGN(32)
  {
    *(.bss.handgpt_npu)
    *(.bss.lcd_crop_and_interpolate_buf)
    *(.bss.lcd_frame_buf)
    *(.bss.camera_frame_buf)
    *(.bss.camera_frame_bayer_to_rgb_buf)
  } > SRAM0
  .bss.at_sram1 (NOLOAD) : ALIGN(32)
  {
    *(.bss.handgpt_preview)
    . = ALIGN(32);
  } > SRAM1
  .bss (NOLOAD) : ALIGN(8)
  {
    __bss_start__ = .;
    *(.bss)
    *(.bss.*)
    *(COMMON)
    . = ALIGN(8);
    __bss_end__ = .;
  } > DTCM
  .nonsecure0_region (NOLOAD) :
  {
    . = ALIGN(16384);
    __ns_region_0_start = .;
    * (eth_buf)
    * (usb_dma_buf)
    * (sd_dma_buf)
    * (.driver.usbd0)
    . = ALIGN(16384);
    __ns_region_0_end = .;
  } > DTCM
  .__app_heap (NOLOAD) : ALIGN(8)
  {
     . = ALIGN(8);
     __RAM_segment_used_end__ = .;
     . = . + 0x00004000;
     . = ALIGN(8);
   } > DTCM
  .heap (NOLOAD) : ALIGN(8)
  {
    __end__ = .;
    PROVIDE(end = .);
    . = . + 0x00020000;
    . = ALIGN(8);
    __HeapLimit = .;
  } > DTCM
  .stack (ORIGIN(DTCM) + LENGTH(DTCM) - 0x00008000) (NOLOAD) : ALIGN(8)
  {
    __StackLimit = .;
    . = . + 0x00008000;
    . = ALIGN(8);
    __StackTop = .;
  } > DTCM
  PROVIDE(__stack = __StackTop);
  .readonly.at_mram : ALIGN(8)
  {
    KEEP(*(.init))
    KEEP(*(.fini))
    . = ALIGN(4);
    PROVIDE_HIDDEN (__preinit_array_start = .);
    KEEP(*(.preinit_array))
    PROVIDE_HIDDEN (__preinit_array_end = .);
    . = ALIGN(4);
    PROVIDE_HIDDEN (__init_array_start = .);
    KEEP(*(SORT(.init_array.*)))
    KEEP(*(.init_array))
    PROVIDE_HIDDEN (__init_array_end = .);
    . = ALIGN(4);
    PROVIDE_HIDDEN (__fini_array_start = .);
    KEEP(*(SORT(.fini_array.*)))
    KEEP(*(.fini_array))
    PROVIDE_HIDDEN (__fini_array_end = .);
    *crtbegin.o(.ctors)
    *crtbegin?.o(.ctors)
    *(EXCLUDE_FILE(*crtend?.o *crtend.o) .ctors)
    *(SORT(.ctors.*))
    *(.ctors)
    *crtbegin.o(.dtors)
    *crtbegin?.o(.dtors)
    *(EXCLUDE_FILE(*crtend?.o *crtend.o) .dtors)
    *(SORT(.dtors.*))
    *(.dtors)
    *(.text*)
    *(.rodata*)
    KEEP(*(.eh_frame*))
    . = ALIGN(16);
  } > MRAM
  .mram_user (NOLOAD) : ALIGN(16)
  {
    __mram_user_start__ = .;
    *(.mram_user)
    *(.mram_user*)
    . = ALIGN(16);
    __mram_user_end__ = .;
  } > MRAM_USER
  ASSERT(__StackLimit >= __HeapLimit, "region RAM overflowed with stack")
  PROVIDE(__stack_limit = __StackLimit);
  PROVIDE(__data_start = 0);
  PROVIDE(__data_source = 0);
  PROVIDE(__data_size = 0);
  PROVIDE(__bss_start = 0);
  PROVIDE(__bss_size = 0);
  PROVIDE(__heap_start = 0);
  PROVIDE(__heap_end = 0);
  __tdata_size = __tdata_end - __tls_base;
  __tbss_size = __tbss_end - __tdata_end;
  __tls_end = __tbss_end;
}
