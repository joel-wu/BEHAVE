module glitch_free_mux(
    input logic clk1_level, clk2_level, sel, rst_n, output wire clkout
);
    logic enable1=0, enable2=0;
    always_ff @(negedge clk1_level or negedge rst_n)
        if (!rst_n) enable1 <= 0;
        else enable1 <= !sel && !enable2;
    always_ff @(negedge clk2_level or negedge rst_n)
        if (!rst_n) enable2 <= 0;
        else enable2 <= sel && !enable1;
    assign clkout=(clk1_level && enable1) || (clk2_level && enable2);
endmodule
