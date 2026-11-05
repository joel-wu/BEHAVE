module cryptography_pqc_kyber_kyber_cbd3 (
    input  logic        clk,
    input  logic        rst,

    input  logic        in_valid,
    output logic        in_ready,
    input  logic [23:0] in_t,

    output logic        out_valid,
    input  logic        out_ready,
    output logic [3:0]  out_c0,
    output logic [3:0]  out_c1,
    output logic [3:0]  out_c2,
    output logic [3:0]  out_c3
);

logic busy;

wire input_accepted;
wire output_consumed;

logic [23:0] d0_w;
logic [23:0] d1_w;
logic [23:0] d2_w;
logic [24:0] d_w;

logic [2:0] a0_w, b0_w;
logic [2:0] a1_w, b1_w;
logic [2:0] a2_w, b2_w;
logic [2:0] a3_w, b3_w;

logic signed [3:0] c0_w, c1_w, c2_w, c3_w;

assign input_accepted  = in_valid & in_ready;
assign output_consumed = out_valid & out_ready;

assign in_ready  = ~busy | output_consumed;
assign out_valid = busy;

always_comb begin
    d0_w = in_t & 24'h249249;
    d1_w = (in_t >> 1) & 24'h249249;
    d2_w = (in_t >> 2) & 24'h249249;
    d_w  = {1'b0, d0_w} + {1'b0, d1_w} + {1'b0, d2_w};

    a0_w = d_w[2:0];
    b0_w = d_w[5:3];
    a1_w = d_w[8:6];
    b1_w = d_w[11:9];
    a2_w = d_w[14:12];
    b2_w = d_w[17:15];
    a3_w = d_w[20:18];
    b3_w = d_w[23:21];

    c0_w = $signed({1'b0, a0_w}) - $signed({1'b0, b0_w});
    c1_w = $signed({1'b0, a1_w}) - $signed({1'b0, b1_w});
    c2_w = $signed({1'b0, a2_w}) - $signed({1'b0, b2_w});
    c3_w = $signed({1'b0, a3_w}) - $signed({1'b0, b3_w});
end

always_ff @(posedge clk or posedge rst) begin
    if (rst) begin
        busy   <= 1'b0;
        out_c0 <= 4'b0000;
        out_c1 <= 4'b0000;
        out_c2 <= 4'b0000;
        out_c3 <= 4'b0000;
    end else if (input_accepted) begin
        busy   <= 1'b1;
        out_c0 <= c0_w;
        out_c1 <= c1_w;
        out_c2 <= c2_w;
        out_c3 <= c3_w;
    end else if (output_consumed) begin
        busy <= 1'b0;
    end
end

endmodule