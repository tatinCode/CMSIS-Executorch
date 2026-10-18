#pragma once
#include <cstddef>
#include <cstdint>
namespace handgpt {
constexpr size_t kAslClassCount = 26;
constexpr size_t kAslLetterCount = 24;
enum class AslLetter : uint8_t { None, A, B, C, D, E, F, G, H, I, K, L, M, N, O, P, Q, R, S, T, U, V, W, X, Y };
constexpr char kAslLetters[] = " ABCDEFGHIKLMNOPQRSTUVWXY";
constexpr const char* kAslNames[] = { "--", "A", "B", "C", "D", "E", "F", "G", "H", "I", "K", "L", "M", "N", "O", "P", "Q", "R", "S", "T", "U", "V", "W", "X", "Y" };
constexpr char kAslClassOrder[] = "A,B,C,D,E,F,G,H,I,K,L,M,N,O,P,Q,R,S,T,U,V,W,X,Y,NO_HAND,OTHER";
} // namespace handgpt
