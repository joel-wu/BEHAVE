module q15_correlation_window (
    input  logic         clk,
    input  logic         rst,

    input  logic         in_valid,
    output logic         in_ready,
    input  logic  [2:0]  in_lag,
    input  logic signed [15:0] in_a0,
    input  logic signed [15:0] in_a1,
    input  logic signed [15:0] in_a2,
    input  logic signed [15:0] in_a3,
    input  logic signed [15:0] in_b0,
    input  logic signed [15:0] in_b1,
    input  logic signed [15:0] in_b2,

    output logic         out_valid,
    input  logic         out_ready,
    output logic signed [15:0] out_corr
);

logic busy;
wire input_accepted;
wire output_consumed;

logic signed [63:0] acc_calc;
logic signed [63:0] shifted_calc;

function automatic logic signed [15:0] sat16(input logic signed [63:0] value);
    begin
        if (value > 64'sd32767)
            sat16 = 16'sd32767;
        else if (value < -64'sd32768)
            sat16 = -16'sd32768;
        else
            sat16 = value[15:0];
    end
endfunction

always_comb begin
    acc_calc = 64'sd0;
    unique case (in_lag)
        3'd1: acc_calc = $signed(in_a0) * $signed(in_b2);
        3'd2: acc_calc = ($signed(in_a0) * $signed(in_b1)) +
                         ($signed(in_a1) * $signed(in_b2));
        3'd3: acc_calc = ($signed(in_a0) * $signed(in_b0)) +
                         ($signed(in_a1) * $signed(in_b1)) +
                         ($signed(in_a2) * $signed(in_b2));
        3'd4: acc_calc = ($signed(in_a1) * $signed(in_b0)) +
                         ($signed(in_a2) * $signed(in_b1)) +
                         ($signed(in_a3) * $signed(in_b2));
        3'd5: acc_calc = ($signed(in_a2) * $signed(in_b0)) +
                         ($signed(in_a3) * $signed(in_b1));
        3'd6: acc_calc = $signed(in_a3) * $signed(in_b0);
        3'd0: acc_calc = 64'sd0;
        3'd7: acc_calc = 64'sd0;
    endcase
    shifted_calc = acc_calc >>> 15;
end

assign out_valid = busy;
assign in_ready = ~busy | output_consumed;

assign input_accepted = in_valid & in_ready;
assign output_consumed = out_valid & out_ready;

always_ff @(posedge clk) begin
    if (rst) begin
        busy <= 1'b0;
        out_corr <= 16'sd0;
    end else if (input_accepted) begin
        out_corr <= sat16(shifted_calc);
        busy <= 1'b1;
    end else if (output_consumed) begin
        busy <= 1'b0;
    end
end

endmodule
