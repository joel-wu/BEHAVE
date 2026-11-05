// A replay owns one CPU grant. Verilator's context otherwise defaults to all
// visible CPUs, even for a model compiled with --threads 1. Set the context
// before cocotb constructs Vtop; keep its event loop and callbacks unchanged.
#define main behave_cocotb_main
#include "verilator.cpp"
#undef main

int main(int argc, char** argv) {
    Verilated::threadContextp()->threads(1);
    return behave_cocotb_main(argc, argv);
}
