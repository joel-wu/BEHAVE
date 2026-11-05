module fifo_async #(
    parameter int DATA_WIDTH = 32,
    parameter int DEPTH = 16
) (
    input logic w_clk, w_rst, w_inc,
    input logic [DATA_WIDTH-1:0] w_data,
    output logic w_full,
    input logic r_clk, r_rst, r_inc,
    output logic r_empty,
    output logic [DATA_WIDTH-1:0] r_data
);
    localparam int A = $clog2(DEPTH);
    localparam logic [A:0] FULL_XOR = (DEPTH + DEPTH/2);
    logic [DATA_WIDTH-1:0] mem [0:DEPTH-1];
    logic [A:0] wp, rp, rq1, rq2, wq1, wq2;
    wire [A:0] wgray = wp ^ (wp >> 1);
    wire [A:0] rgray = rp ^ (rp >> 1);
    assign w_full = (wgray == (rq2 ^ FULL_XOR));
    assign r_empty = (rgray == wq2);
    assign r_data = r_empty ? '0 : mem[rp[A-1:0]];
    always_ff @(posedge w_clk) begin
        if (w_rst) begin
            wp <= 0; rq1 <= 0; rq2 <= 0;
            for (int i=0; i<DEPTH; i++) mem[i] <= 0;
        end else begin
            rq1 <= rgray;
            rq2 <= rq1;
            if (w_inc && !w_full) begin
                mem[wp[A-1:0]] <= w_data;
                wp <= wp + 1'b1;
            end
        end
    end
    always_ff @(posedge r_clk) begin
        if (r_rst) begin rp <= 0; wq1 <= 0; wq2 <= 0; end
        else begin
            wq1 <= wgray;
            wq2 <= wq1;
            if (r_inc && !r_empty) rp <= rp + 1'b1;
        end
    end
endmodule
