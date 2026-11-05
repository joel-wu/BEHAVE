module fir_decimate_fast_q15 #(
    parameter int unsigned NUM_TAPS = 8,
    parameter int unsigned M = 4,
    parameter logic [16*NUM_TAPS-1:0] COEFFS = 128'h10003000c0002000f0006000e0004000
) (
    input logic clk, rst,
    input logic in_valid,
    output logic in_ready,
    input logic [15:0] in_sample,
    output logic out_valid,
    input logic out_ready,
    output logic [15:0] out_sample
);
    logic signed [15:0] history [0:NUM_TAPS-1];
    logic signed [31:0] acc, shifted;
    logic signed [15:0] filtered;
    logic pending;
    assign in_ready = !pending || out_ready;
    assign out_valid = pending;

    always_comb begin
        acc = 0;
        for (int i=0; i<NUM_TAPS; i++) begin
            if (i == NUM_TAPS-1)
                acc = acc + $signed(in_sample) * $signed(COEFFS[16*i +: 16]);
            else
                acc = acc + history[i+1] * $signed(COEFFS[16*i +: 16]);
        end
        shifted = acc >>> 15;
        if (shifted > 32767) filtered = 16'sd32767;
        else if (shifted < -32768) filtered = -16'sd32768;
        else filtered = shifted[15:0];
    end
    localparam int PHASE_W = (M <= 1) ? 1 : $clog2(M);
    logic [PHASE_W-1:0] phase;
    logic signed [15:0] saved;
    always_ff @(posedge clk) begin
        if (rst) begin
            for (int i=0; i<NUM_TAPS; i++) history[i] <= 0;
            pending <= 0;
            out_sample <= 0;
            phase <= 0;
            saved <= 0;
        end else begin
            if (out_valid && out_ready) pending <= 0;
            if (in_valid && in_ready) begin
                for (int i=0; i<NUM_TAPS-1; i++) history[i] <= history[i+1];
                history[NUM_TAPS-1] <= $signed(in_sample);
                if (phase == 0) saved <= filtered;
                if (phase == M-1) begin
                    out_sample <= (M == 1) ? filtered : saved;
                    pending <= 1;
                    phase <= 0;
                end else phase <= phase + 1'b1;
            end
        end
    end
endmodule
