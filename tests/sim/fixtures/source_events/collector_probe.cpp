#include "VM.h"
#include "verilated.h"
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <vector>

extern "C" const char* behave_native_events_identity_v1();
extern "C" int behave_native_events_begin_v1(uint64_t);
extern "C" int behave_native_events_end_v1(uint64_t, uint64_t*, uint64_t, uint64_t*);
extern "C" int behave_native_events_finish_v1();

static void check(int code) {
    if (code) { std::fprintf(stderr, "collector error %d\n", code); std::exit(1); }
}

int main(int argc, char** argv) {
    if (argc != 3 || std::strcmp(argv[1], behave_native_events_identity_v1())) return 2;
    const auto count = std::strtoul(argv[2], nullptr, 10);
    std::vector<uint64_t> words(count);
    VerilatedContext ctx;
    ctx.commandArgs(argc, argv);
    uint64_t sequence = 0;
    // Include construction in the first interval: a startup callback cannot
    // disappear before a later Python/testbench attachment.
    check(behave_native_events_begin_v1(sequence));
    VM dut{&ctx};
    auto step = [&](const char* phase) {
        if (sequence) check(behave_native_events_begin_v1(sequence));
        dut.eval();
        uint64_t callbacks = 0;
        check(behave_native_events_end_v1(sequence++, words.data(), count, &callbacks));
        std::printf("ROW %s %u %u", phase, dut.q, dut.seen);
        for (size_t index = 0; index < words.size(); ++index)
            for (unsigned bit = 0; bit < 64; ++bit)
                if (words[index] & (uint64_t{1} << bit))
                    std::printf(" %zu", index * 64 + bit);
        std::printf("\n");
    };
    dut.clk = 0; dut.rst = 0; dut.sel = 3; step("init");
    dut.rst = 1; step("reset");
    step("held-reset");
    dut.rst = 0; step("release");
    dut.clk = 1; step("clock-1");
    dut.clk = 0; step("inactive-1");
    dut.sel = 1; dut.clk = 1; step("clock-2");
    dut.clk = 0; step("inactive-2");
    dut.sel = 3; dut.clk = 1; step("clock-3");
    dut.clk = 0; step("inactive-3");
    dut.sel = 0; dut.rst = 1; dut.clk = 1; step("coincident-reset");
    check(behave_native_events_finish_v1());
}
