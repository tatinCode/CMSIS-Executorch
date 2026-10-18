"""Explicit layout profiles; selecting one is never permission to program a board."""

from dataclasses import dataclass


@dataclass(frozen=True)
class Layout:
    name: str
    mram_base: int
    mram_end: int
    user_end: int = 0x80580000
    sram_base: int = 0x02000000
    sram_end: int = 0x02400000
    dtcm_base: int = 0x20000000
    dtcm_end: int = 0x20100000
    reclaims_mram: bool = False

    @property
    def limits(self):
        return {"mram_bytes": self.mram_end - self.mram_base,
                "sram_bytes": self.sram_end - self.sram_base,
                "dtcm_bytes": self.dtcm_end - self.dtcm_base}

    def report(self):
        return {"name": self.name, "mram_base": hex(self.mram_base), "mram_end": hex(self.mram_end),
                "user_end": hex(self.user_end), "sram_base": hex(self.sram_base),
                "dtcm_base": hex(self.dtcm_base), "limits": self.limits,
                "reclaims_default_he_and_user_space": self.reclaims_mram,
                "programming_authorized": False}


CURRENT = Layout("current-E8-HP", 0x80200000, 0x80400000)
HP_ONLY_LARGE = Layout("hp-only-large", 0x80001000, 0x80500000, reclaims_mram=True)
LAYOUTS = {layout.name: layout for layout in (CURRENT, HP_ONLY_LARGE)}


def select_layout(name):
    if name not in LAYOUTS:
        raise ValueError(f"Unknown layout profile: {name}")
    return LAYOUTS[name]
