module ai_ml_ssm_based_based_feature_map (
    input  logic         clk,
    input  logic         rst,

    input  logic         in_valid,
    output logic         in_ready,
    input  logic [127:0] in_q,
    input  logic [127:0] in_k,

    output logic         out_valid,
    input  logic         out_ready,
    output logic [20:0]  out_s
);

logic busy;

wire input_accepted;
wire output_consumed;

assign input_accepted  = in_valid & in_ready;
assign output_consumed = out_valid & out_ready;

assign in_ready  = ~busy | output_consumed;
assign out_valid = busy;

function automatic logic [20:0] compute_feature(
    input logic [127:0] q_pack,
    input logic [127:0] k_pack
);
    logic signed [15:0] q0, q1, q2, q3, q4, q5, q6, q7;
    logic signed [15:0] k0, k1, k2, k3, k4, k5, k6, k7;

    logic signed [31:0] p0, p1, p2, p3, p4, p5, p6, p7;
    logic signed [34:0] dot_q30;
    logic signed [34:0] x_q30;
    logic signed [35:0] term1_q30;
    logic signed [69:0] x2_q60;
    logic signed [39:0] half_x2_q30;
    logic signed [40:0] phi_q30;
    logic        [40:0] rounded_mag_q15;

    begin
        compute_feature = 21'd0;

        q0 = q_pack[15:0];
        q1 = q_pack[31:16];
        q2 = q_pack[47:32];
        q3 = q_pack[63:48];
        q4 = q_pack[79:64];
        q5 = q_pack[95:80];
        q6 = q_pack[111:96];
        q7 = q_pack[127:112];

        k0 = k_pack[15:0];
        k1 = k_pack[31:16];
        k2 = k_pack[47:32];
        k3 = k_pack[63:48];
        k4 = k_pack[79:64];
        k5 = k_pack[95:80];
        k6 = k_pack[111:96];
        k7 = k_pack[127:112];

        p0 = q0 * k0;
        p1 = q1 * k1;
        p2 = q2 * k2;
        p3 = q3 * k3;
        p4 = q4 * k4;
        p5 = q5 * k5;
        p6 = q6 * k6;
        p7 = q7 * k7;

        dot_q30 = $signed(p0) + $signed(p1) + $signed(p2) + $signed(p3) +
                  $signed(p4) + $signed(p5) + $signed(p6) + $signed(p7);

        x_q30     = dot_q30;
        term1_q30 = 36'sd1073741824 + $signed(x_q30);

        x2_q60        = $signed(x_q30) * $signed(x_q30);
        half_x2_q30   = x2_q60 >>> 31;
        phi_q30       = $signed(term1_q30) + $signed(half_x2_q30);

        rounded_mag_q15 = ($unsigned(phi_q30) + 41'd16384) >> 15;
        compute_feature = rounded_mag_q15[20:0];
    end
endfunction

always_ff @(posedge clk or posedge rst) begin
    if (rst) begin
        busy  <= 1'b0;
        out_s <= 21'd0;
    end else if (input_accepted) begin
        out_s <= compute_feature(in_q, in_k);
        busy  <= 1'b1;
    end else if (output_consumed) begin
        busy <= 1'b0;
    end
end

endmodule
