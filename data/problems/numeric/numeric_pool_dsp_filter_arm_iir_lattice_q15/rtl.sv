module q15_iir_lattice_filter_4stage (
    input  logic         clk,
    input  logic         rst,

    input  logic         in_valid,
    output logic         in_ready,
    input  logic [15:0]  in_sample,

    output logic         out_valid,
    input  logic         out_ready,
    output logic [15:0]  out_sample
);

logic busy;
wire  input_accepted;
wire  output_consumed;

logic signed [15:0] state0, state1, state2, state3;
logic signed [15:0] out_sample_s;

localparam logic signed [15:0] K0 = 16'sh2000;
localparam logic signed [15:0] K1 = 16'shE000;
localparam logic signed [15:0] K2 = 16'sh1000;
localparam logic signed [15:0] K3 = 16'shF000;

localparam logic signed [15:0] V0 = 16'sh1000;
localparam logic signed [15:0] V1 = 16'sh2000;
localparam logic signed [15:0] V2 = 16'shE000;
localparam logic signed [15:0] V3 = 16'sh0800;
localparam logic signed [15:0] V4 = 16'shF800;

function automatic logic signed [15:0] sat16(input logic signed [31:0] value);
    begin
        if (value > 32'sd32767)
            sat16 = 16'sd32767;
        else if (value < -32'sd32768)
            sat16 = -16'sd32768;
        else
            sat16 = value[15:0];
    end
endfunction

logic signed [15:0] x_s;
logic signed [15:0] g_prev0, g_prev1, g_prev2, g_prev3;
logic signed [15:0] f0, f1, f2, f3, f4;
logic signed [31:0] mul_fk0, mul_fk1, mul_fk2, mul_fk3;
logic signed [31:0] mul_gk0, mul_gk1, mul_gk2, mul_gk3;
logic signed [31:0] prod_fk0, prod_fk1, prod_fk2, prod_fk3;
logic signed [31:0] prod_gk0, prod_gk1, prod_gk2, prod_gk3;
logic signed [15:0] g_new0, g_new1, g_new2, g_new3;
logic signed [31:0] acc0, acc1, acc2, acc3, acc4;

always_comb begin
    x_s     = $signed(in_sample);
    g_prev0 = state0;
    g_prev1 = state1;
    g_prev2 = state2;
    g_prev3 = state3;

    f0 = x_s;
    acc0 = 32'sd0;

    mul_fk0  = g_prev0 * K0;
    prod_fk0 = mul_fk0 >>> 15;
    f1       = sat16($signed({{16{f0[15]}}, f0}) - prod_fk0);
    mul_gk0  = f1 * K0;
    prod_gk0 = mul_gk0 >>> 15;
    g_new0   = sat16(prod_gk0 + $signed({{16{g_prev0[15]}}, g_prev0}));
    acc1     = acc0 + (g_new0 * V0);

    mul_fk1  = g_prev1 * K1;
    prod_fk1 = mul_fk1 >>> 15;
    f2       = sat16($signed({{16{f1[15]}}, f1}) - prod_fk1);
    mul_gk1  = f2 * K1;
    prod_gk1 = mul_gk1 >>> 15;
    g_new1   = sat16(prod_gk1 + $signed({{16{g_prev1[15]}}, g_prev1}));
    acc2     = acc1 + (g_new1 * V1);

    mul_fk2  = g_prev2 * K2;
    prod_fk2 = mul_fk2 >>> 15;
    f3       = sat16($signed({{16{f2[15]}}, f2}) - prod_fk2);
    mul_gk2  = f3 * K2;
    prod_gk2 = mul_gk2 >>> 15;
    g_new2   = sat16(prod_gk2 + $signed({{16{g_prev2[15]}}, g_prev2}));
    acc3     = acc2 + (g_new2 * V2);

    mul_fk3  = g_prev3 * K3;
    prod_fk3 = mul_fk3 >>> 15;
    f4       = sat16($signed({{16{f3[15]}}, f3}) - prod_fk3);
    mul_gk3  = f4 * K3;
    prod_gk3 = mul_gk3 >>> 15;
    g_new3   = sat16(prod_gk3 + $signed({{16{g_prev3[15]}}, g_prev3}));
    acc4     = acc3 + (g_new3 * V3) + (f4 * V4);
end

assign input_accepted  = in_valid & in_ready;
assign output_consumed = out_valid & out_ready;

assign in_ready   = ~busy | output_consumed;
assign out_valid  = busy;
assign out_sample = busy ? out_sample_s : 16'h0000;

always_ff @(posedge clk) begin
    if (rst) begin
        busy         <= 1'b0;
        out_sample_s <= 16'sh0000;
        state0       <= 16'sh0000;
        state1       <= 16'sh0000;
        state2       <= 16'sh0000;
        state3       <= 16'sh0000;
    end else if (input_accepted) begin
        out_sample_s <= sat16(acc4 >>> 15);
        state0       <= g_new1;
        state1       <= g_new2;
        state2       <= g_new3;
        state3       <= f4;
        busy         <= 1'b1;
    end else if (output_consumed) begin
        busy         <= 1'b0;
        out_sample_s <= 16'sh0000;
    end
end

endmodule
