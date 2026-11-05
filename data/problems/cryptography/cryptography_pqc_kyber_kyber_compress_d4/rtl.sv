module KyberPolyCompressD4 (
    input  logic        clk,
    input  logic        rst,

    input  logic        in_valid,
    output logic        in_ready,
    input  logic [15:0] in_coeff0,
    input  logic [15:0] in_coeff1,
    input  logic [15:0] in_coeff2,
    input  logic [15:0] in_coeff3,
    input  logic [15:0] in_coeff4,
    input  logic [15:0] in_coeff5,
    input  logic [15:0] in_coeff6,
    input  logic [15:0] in_coeff7,

    output logic        out_valid,
    output logic out_error,
    input  logic        out_ready,
    output logic [7:0]  out_byte0,
    output logic [7:0]  out_byte1,
    output logic [7:0]  out_byte2,
    output logic [7:0]  out_byte3
);

    logic busy;
    wire invalid_input = ($signed(in_coeff0) <= -16'sd3329 || $signed(in_coeff0) >= 16'sd3329) ||
        ($signed(in_coeff1) <= -16'sd3329 || $signed(in_coeff1) >= 16'sd3329) ||
        ($signed(in_coeff2) <= -16'sd3329 || $signed(in_coeff2) >= 16'sd3329) ||
        ($signed(in_coeff3) <= -16'sd3329 || $signed(in_coeff3) >= 16'sd3329) ||
        ($signed(in_coeff4) <= -16'sd3329 || $signed(in_coeff4) >= 16'sd3329) ||
        ($signed(in_coeff5) <= -16'sd3329 || $signed(in_coeff5) >= 16'sd3329) ||
        ($signed(in_coeff6) <= -16'sd3329 || $signed(in_coeff6) >= 16'sd3329) ||
        ($signed(in_coeff7) <= -16'sd3329 || $signed(in_coeff7) >= 16'sd3329);
    wire input_accepted;
    wire output_consumed;

    function automatic logic [3:0] compress_coeff(input logic [15:0] coeff_bits);
        logic signed [15:0] c;
        logic signed [16:0] u;
        logic signed [20:0] d0_a;
        logic signed [37:0] d0_m;
    begin
        c = $signed(coeff_bits);
        if (c < 0) begin
            u = c + 17'sd3329;
        end else begin
            u = c;
        end
        d0_a = (u <<< 4) + 21'sd1665;
        d0_m = d0_a * 38'sd80635;
        compress_coeff = d0_m[31:28];
    end
    endfunction

    wire [3:0] t0_w;
    wire [3:0] t1_w;
    wire [3:0] t2_w;
    wire [3:0] t3_w;
    wire [3:0] t4_w;
    wire [3:0] t5_w;
    wire [3:0] t6_w;
    wire [3:0] t7_w;

    assign t0_w = compress_coeff(in_coeff0);
    assign t1_w = compress_coeff(in_coeff1);
    assign t2_w = compress_coeff(in_coeff2);
    assign t3_w = compress_coeff(in_coeff3);
    assign t4_w = compress_coeff(in_coeff4);
    assign t5_w = compress_coeff(in_coeff5);
    assign t6_w = compress_coeff(in_coeff6);
    assign t7_w = compress_coeff(in_coeff7);

    assign input_accepted  = in_valid & in_ready;
    assign output_consumed = out_valid & out_ready;

    assign in_ready  = ~busy | output_consumed;
    assign out_valid = busy;

    always_ff @(posedge clk or posedge rst) begin
        if (rst) begin
            busy      <= 1'b0;
            out_error <= 1'b0;
            out_byte0 <= 8'd0;
            out_byte1 <= 8'd0;
            out_byte2 <= 8'd0;
            out_byte3 <= 8'd0;
        end else if (input_accepted) begin
            out_byte0 <= invalid_input ? '0 : ({t1_w, t0_w});
            out_byte1 <= invalid_input ? '0 : ({t3_w, t2_w});
            out_byte2 <= invalid_input ? '0 : ({t5_w, t4_w});
            out_byte3 <= invalid_input ? '0 : ({t7_w, t6_w});
            busy      <= 1'b1;
            out_error <= invalid_input;
        end else if (output_consumed) begin
            busy      <= 1'b0;
            out_error <= 1'b0;
            out_byte0 <= 8'd0;
            out_byte1 <= 8'd0;
            out_byte2 <= 8'd0;
            out_byte3 <= 8'd0;
        end
    end

endmodule
