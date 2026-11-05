module signal_processing_comm_filter_bessel (
    input  logic        clk,
    input  logic        rst,

    input  logic        in_valid,
    output logic        in_ready,
    input  logic [7:0]  in_n,

    output logic        out_valid,
    input  logic        out_ready,
    output logic [31:0] out_p0_re,
    output logic [31:0] out_p0_im,
    output logic [31:0] out_p1_re,
    output logic [31:0] out_p1_im,
    output logic [31:0] out_p2_re,
    output logic [31:0] out_p2_im,
    output logic [31:0] out_p3_re,
    output logic [31:0] out_p3_im,
    output logic [31:0] out_p4_re,
    output logic [31:0] out_p4_im,
    output logic [31:0] out_p5_re,
    output logic [31:0] out_p5_im,
    output logic [31:0] out_p6_re,
    output logic [31:0] out_p6_im,
    output logic [31:0] out_p7_re,
    output logic [31:0] out_p7_im,
    output logic [31:0] out_k_re,
    output logic [31:0] out_k_im
);

logic busy;

wire input_accepted;
wire output_consumed;

assign input_accepted  = in_valid & in_ready;
assign output_consumed = out_valid & out_ready;

assign in_ready  = ~busy | output_consumed;
assign out_valid = busy;

always_ff @(posedge clk) begin
    if (rst) begin
        busy      <= 1'b0;
        out_p0_re <= 32'h00000000;
        out_p0_im <= 32'h00000000;
        out_p1_re <= 32'h00000000;
        out_p1_im <= 32'h00000000;
        out_p2_re <= 32'h00000000;
        out_p2_im <= 32'h00000000;
        out_p3_re <= 32'h00000000;
        out_p3_im <= 32'h00000000;
        out_p4_re <= 32'h00000000;
        out_p4_im <= 32'h00000000;
        out_p5_re <= 32'h00000000;
        out_p5_im <= 32'h00000000;
        out_p6_re <= 32'h00000000;
        out_p6_im <= 32'h00000000;
        out_p7_re <= 32'h00000000;
        out_p7_im <= 32'h00000000;
        out_k_re  <= 32'h00000000;
        out_k_im  <= 32'h00000000;
    end else if (input_accepted) begin
        busy <= 1'b1;
        unique case (in_n)
            8'd1: begin
                out_p0_re <= 32'hBF99BE61; out_p0_im <= 32'h00000000;
                out_p1_re <= 32'h00000000; out_p1_im <= 32'h00000000;
                out_p2_re <= 32'h00000000; out_p2_im <= 32'h00000000;
                out_p3_re <= 32'h00000000; out_p3_im <= 32'h00000000;
                out_p4_re <= 32'h00000000; out_p4_im <= 32'h00000000;
                out_p5_re <= 32'h00000000; out_p5_im <= 32'h00000000;
                out_p6_re <= 32'h00000000; out_p6_im <= 32'h00000000;
                out_p7_re <= 32'h00000000; out_p7_im <= 32'h00000000;
                out_k_re  <= 32'hBF99BE61; out_k_im  <= 32'h00000000;
            end
            8'd2: begin
                out_p0_re <= 32'hBF85255B; out_p0_im <= 32'h3F19BE61;
                out_p1_re <= 32'hBF85255B; out_p1_im <= 32'hBF19BE61;
                out_p2_re <= 32'h00000000; out_p2_im <= 32'h00000000;
                out_p3_re <= 32'h00000000; out_p3_im <= 32'h00000000;
                out_p4_re <= 32'h00000000; out_p4_im <= 32'h00000000;
                out_p5_re <= 32'h00000000; out_p5_im <= 32'h00000000;
                out_p6_re <= 32'h00000000; out_p6_im <= 32'h00000000;
                out_p7_re <= 32'h00000000; out_p7_im <= 32'h00000000;
                out_k_re  <= 32'h3FB8AA3B; out_k_im  <= 32'h00000000;
            end
            8'd3: begin
                out_p0_re <= 32'hBF7CDF6F; out_p0_im <= 32'h3F713FD5;
                out_p1_re <= 32'hBF7CDF6F; out_p1_im <= 32'hBF713FD5;
                out_p2_re <= 32'hBF9FAA2F; out_p2_im <= 32'h00000000;
                out_p3_re <= 32'h00000000; out_p3_im <= 32'h00000000;
                out_p4_re <= 32'h00000000; out_p4_im <= 32'h00000000;
                out_p5_re <= 32'h00000000; out_p5_im <= 32'h00000000;
                out_p6_re <= 32'h00000000; out_p6_im <= 32'h00000000;
                out_p7_re <= 32'h00000000; out_p7_im <= 32'h00000000;
                out_k_re  <= 32'hC014CA9E; out_k_im  <= 32'h00000000;
            end
            8'd4: begin
                out_p0_re <= 32'hBF74803A; out_p0_im <= 32'h3F9A6BEF;
                out_p1_re <= 32'hBF74803A; out_p1_im <= 32'hBF9A6BEF;
                out_p2_re <= 32'hBFA84C3A; out_p2_im <= 32'h3EC99423;
                out_p3_re <= 32'hBFA84C3A; out_p3_im <= 32'hBEC99423;
                out_p4_re <= 32'h00000000; out_p4_im <= 32'h00000000;
                out_p5_re <= 32'h00000000; out_p5_im <= 32'h00000000;
                out_p6_re <= 32'h00000000; out_p6_im <= 32'h00000000;
                out_p7_re <= 32'h00000000; out_p7_im <= 32'h00000000;
                out_k_re  <= 32'h408EB8F1; out_k_im  <= 32'h00000000;
            end
            8'd5: begin
                out_p0_re <= 32'hBF6E44F2; out_p0_im <= 32'h3FB701E5;
                out_p1_re <= 32'hBF6E44F2; out_p1_im <= 32'hBFB701E5;
                out_p2_re <= 32'hBFABC7DC; out_p2_im <= 32'h3F329D8D;
                out_p3_re <= 32'hBFABC7DC; out_p3_im <= 32'hBF329D8D;
                out_p4_re <= 32'hBFBAE33E; out_p4_im <= 32'h00000000;
                out_p5_re <= 32'h00000000; out_p5_im <= 32'h00000000;
                out_p6_re <= 32'h00000000; out_p6_im <= 32'h00000000;
                out_p7_re <= 32'h00000000; out_p7_im <= 32'h00000000;
                out_k_re  <= 32'hC11B8DE5; out_k_im  <= 32'hB4BAE33E;
            end
            8'd6: begin
                out_p0_re <= 32'hBF694121; out_p0_im <= 32'h3FD0428A;
                out_p1_re <= 32'hBF694121; out_p1_im <= 32'hBFD0428A;
                out_p2_re <= 32'hBFAD2BA5; out_p2_im <= 32'h3F737BEF;
                out_p3_re <= 32'hBFAD2BA5; out_p3_im <= 32'hBF737BEF;
                out_p4_re <= 32'hBFC4EF45; out_p4_im <= 32'h3EA0DAF1;
                out_p5_re <= 32'hBFC4EF45; out_p5_im <= 32'hBEA0DAF1;
                out_p6_re <= 32'h00000000; out_p6_im <= 32'h00000000;
                out_p7_re <= 32'h00000000; out_p7_im <= 32'h00000000;
                out_k_re  <= 32'h41BB9C9C; out_k_im  <= 32'h00000000;
            end
            8'd7: begin
                out_p0_re <= 32'hBF6509F7; out_p0_im <= 32'h3FE72492;
                out_p1_re <= 32'hBF6509F7; out_p1_im <= 32'hBFE72492;
                out_p2_re <= 32'hBFAD8DD7; out_p2_im <= 32'h3F95F9A6;
                out_p3_re <= 32'hBFAD8DD7; out_p3_im <= 32'hBF95F9A6;
                out_p4_re <= 32'hBFCAE5BC; out_p4_im <= 32'h3F145444;
                out_p5_re <= 32'hBFCAE5BC; out_p5_im <= 32'hBF145444;
                out_p6_re <= 32'hBFD40046; out_p6_im <= 32'h00000000;
                out_p7_re <= 32'h00000000; out_p7_im <= 32'h00000000;
                out_k_re  <= 32'hC2761D44; out_k_im  <= 32'h00000000;
            end
            8'd8: begin
                out_p0_re <= 32'hBF616528; out_p0_im <= 32'h3FFC3A4A;
                out_p1_re <= 32'hBF616528; out_p1_im <= 32'hBFFC3A4A;
                out_p2_re <= 32'hBFAD67D2; out_p2_im <= 32'h3FAF3CD8;
                out_p3_re <= 32'hBFAD67D2; out_p3_im <= 32'hBFAF3CD8;
                out_p4_re <= 32'hBFCE9D19; out_p4_im <= 32'h3F4FB4A9;
                out_p5_re <= 32'hBFCE9D19; out_p5_im <= 32'hBF4FB4A9;
                out_p6_re <= 32'hBFDDD1B7; out_p6_im <= 32'h3E89C3CC;
                out_p7_re <= 32'hBFDDD1B7; out_p7_im <= 32'hBE89C3CC;
                out_k_re  <= 32'h432D7506; out_k_im  <= 32'h36C00000;
            end
            default: begin
                out_p0_re <= 32'h00000000; out_p0_im <= 32'h00000000;
                out_p1_re <= 32'h00000000; out_p1_im <= 32'h00000000;
                out_p2_re <= 32'h00000000; out_p2_im <= 32'h00000000;
                out_p3_re <= 32'h00000000; out_p3_im <= 32'h00000000;
                out_p4_re <= 32'h00000000; out_p4_im <= 32'h00000000;
                out_p5_re <= 32'h00000000; out_p5_im <= 32'h00000000;
                out_p6_re <= 32'h00000000; out_p6_im <= 32'h00000000;
                out_p7_re <= 32'h00000000; out_p7_im <= 32'h00000000;
                out_k_re  <= 32'h00000000; out_k_im  <= 32'h00000000;
            end
        endcase
    end else if (output_consumed) begin
        busy <= 1'b0;
    end
end

endmodule
