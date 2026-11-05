module ai_ml_vision_winograd_winograd_f23_input_xform (
    input  logic clk,
    input  logic rst,

    input  logic in_valid,
    output logic in_ready,
    input  logic signed [15:0] in_d00,
    input  logic signed [15:0] in_d01,
    input  logic signed [15:0] in_d02,
    input  logic signed [15:0] in_d03,
    input  logic signed [15:0] in_d10,
    input  logic signed [15:0] in_d11,
    input  logic signed [15:0] in_d12,
    input  logic signed [15:0] in_d13,
    input  logic signed [15:0] in_d20,
    input  logic signed [15:0] in_d21,
    input  logic signed [15:0] in_d22,
    input  logic signed [15:0] in_d23,
    input  logic signed [15:0] in_d30,
    input  logic signed [15:0] in_d31,
    input  logic signed [15:0] in_d32,
    input  logic signed [15:0] in_d33,

    output logic out_valid,
    input  logic out_ready,
    output logic signed [19:0] out_t00,
    output logic signed [19:0] out_t01,
    output logic signed [19:0] out_t02,
    output logic signed [19:0] out_t03,
    output logic signed [19:0] out_t10,
    output logic signed [19:0] out_t11,
    output logic signed [19:0] out_t12,
    output logic signed [19:0] out_t13,
    output logic signed [19:0] out_t20,
    output logic signed [19:0] out_t21,
    output logic signed [19:0] out_t22,
    output logic signed [19:0] out_t23,
    output logic signed [19:0] out_t30,
    output logic signed [19:0] out_t31,
    output logic signed [19:0] out_t32,
    output logic signed [19:0] out_t33
);

logic busy;

wire input_accepted;
wire output_consumed;

assign out_valid = busy;
assign in_ready  = ~busy | output_consumed;

assign input_accepted  = in_valid & in_ready;
assign output_consumed = out_valid & out_ready;

always_ff @(posedge clk or posedge rst) begin
    if (rst) begin
        busy    <= 1'b0;
        out_t00 <= 20'sd0;
        out_t01 <= 20'sd0;
        out_t02 <= 20'sd0;
        out_t03 <= 20'sd0;
        out_t10 <= 20'sd0;
        out_t11 <= 20'sd0;
        out_t12 <= 20'sd0;
        out_t13 <= 20'sd0;
        out_t20 <= 20'sd0;
        out_t21 <= 20'sd0;
        out_t22 <= 20'sd0;
        out_t23 <= 20'sd0;
        out_t30 <= 20'sd0;
        out_t31 <= 20'sd0;
        out_t32 <= 20'sd0;
        out_t33 <= 20'sd0;
    end else if (input_accepted) begin
        out_t00 <= $signed(in_d00) - $signed(in_d20) - $signed(in_d02) + $signed(in_d22);
        out_t01 <= $signed(in_d01) - $signed(in_d21) + $signed(in_d02) - $signed(in_d22);
        out_t02 <= $signed(in_d02) - $signed(in_d22) - $signed(in_d01) + $signed(in_d21);
        out_t03 <= $signed(in_d01) - $signed(in_d21) - $signed(in_d03) + $signed(in_d23);

        out_t10 <= $signed(in_d10) + $signed(in_d20) - $signed(in_d12) - $signed(in_d22);
        out_t11 <= $signed(in_d11) + $signed(in_d21) + $signed(in_d12) + $signed(in_d22);
        out_t12 <= $signed(in_d12) + $signed(in_d22) - $signed(in_d11) - $signed(in_d21);
        out_t13 <= $signed(in_d11) + $signed(in_d21) - $signed(in_d13) - $signed(in_d23);

        out_t20 <= $signed(in_d20) - $signed(in_d10) - $signed(in_d22) + $signed(in_d12);
        out_t21 <= $signed(in_d21) - $signed(in_d11) + $signed(in_d22) - $signed(in_d12);
        out_t22 <= $signed(in_d22) - $signed(in_d12) - $signed(in_d21) + $signed(in_d11);
        out_t23 <= $signed(in_d21) - $signed(in_d11) - $signed(in_d23) + $signed(in_d13);

        out_t30 <= $signed(in_d10) - $signed(in_d30) - $signed(in_d12) + $signed(in_d32);
        out_t31 <= $signed(in_d11) - $signed(in_d31) + $signed(in_d12) - $signed(in_d32);
        out_t32 <= $signed(in_d12) - $signed(in_d32) - $signed(in_d11) + $signed(in_d31);
        out_t33 <= $signed(in_d11) - $signed(in_d31) - $signed(in_d13) + $signed(in_d33);

        busy <= 1'b1;
    end else if (output_consumed) begin
        busy <= 1'b0;
    end
end

endmodule