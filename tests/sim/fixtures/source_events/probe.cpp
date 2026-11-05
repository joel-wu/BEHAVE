#include "VM.h"
#include "verilated.h"
#include <cstdio>
#include <cstdlib>
#include <set>

// Test-owned write-only sink. It cannot read or write the DUT. Production
// transport, hierarchy/context binding and event budgets are separate gates.
static std::set<unsigned> hits;
extern "C" void __behave_native_event_hit(unsigned id) {
    if (id >= 1000000) std::abort();
    hits.insert(id);
}

int main(int argc, char** argv) {
    VerilatedContext ctx;
    ctx.commandArgs(argc, argv);
    VM dut{&ctx};
    auto step = [&](const char* phase) {
        hits.clear();
        dut.eval();
        std::printf("ROW %s %u %u", phase, dut.q, dut.seen);
        for (unsigned id : hits) std::printf(" %u", id);
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
}
