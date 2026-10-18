"""Explicit, versioned output orders. No torch dependency or implicit remapping."""

LEGACY_CLASSES = ("A", "B", "C", "L", "Y", "NO_HAND", "OTHER")
STATIC_LETTERS = tuple(c for c in "ABCDEFGHIJKLMNOPQRSTUVWXYZ" if c not in "JZ")
STATIC_CLASSES = (*STATIC_LETTERS, "NO_HAND", "OTHER")
DATASET_CLASSES = STATIC_CLASSES[:-1]  # OTHER comes from reviewed camera negatives only.
EXCLUDED_FOLDERS = ("J", "Z", "del", "space")


def validate_classes(classes):
    if not isinstance(classes, (list, tuple)) or tuple(classes) not in (LEGACY_CLASSES, STATIC_CLASSES):
        raise ValueError("Unsupported ASL class order; use the exact legacy-7 or static-26 contract")
    return tuple(classes)


def class_header(classes):
    classes = validate_classes(classes)
    letters = classes[:-2]
    members = ", ".join(("None", *letters))
    names = ", ".join('"' + c + '"' for c in ("--", *letters))
    return ("#pragma once\n#include <cstddef>\n#include <cstdint>\nnamespace handgpt {\n"
            f"constexpr size_t kAslClassCount = {len(classes)};\n"
            f"constexpr size_t kAslLetterCount = {len(letters)};\n"
            f"enum class AslLetter : uint8_t {{ {members} }};\n"
            f"constexpr char kAslLetters[] = \" {''.join(letters)}\";\n"
            f"constexpr const char* kAslNames[] = {{ {names} }};\n"
            f"constexpr char kAslClassOrder[] = \"{','.join(classes)}\";\n"
            "} // namespace handgpt\n")
