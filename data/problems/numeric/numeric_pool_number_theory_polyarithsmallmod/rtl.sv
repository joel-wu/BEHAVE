module negacyclic_shift_poly_coeffmod (
    input  logic         clk,
    input  logic         rst,

    input  logic         in_valid,
    output logic         in_ready,
    input  logic [63:0]  in_c0,
    input  logic [63:0]  in_c1,
    input  logic [63:0]  in_c2,
    input  logic [63:0]  in_c3,
    input  logic [63:0]  in_c4,
    input  logic [63:0]  in_c5,
    input  logic [63:0]  in_c6,
    input  logic [63:0]  in_c7,
    input  logic [2:0]   in_shift,
    input  logic [63:0]  in_modulus,

    output logic         out_valid,
    input  logic         out_ready,
    output logic [63:0]  out_c0,
    output logic [63:0]  out_c1,
    output logic [63:0]  out_c2,
    output logic [63:0]  out_c3,
    output logic [63:0]  out_c4,
    output logic [63:0]  out_c5,
    output logic [63:0]  out_c6,
    output logic [63:0]  out_c7
);

logic busy;
wire  input_accepted;
wire  output_consumed;

function automatic logic [63:0] neg_q(
    input logic [63:0] coeff,
    input logic [63:0] q
);
    begin
        if (coeff == 64'd0) begin
            neg_q = 64'd0;
        end else begin
            neg_q = q - coeff;
        end
    end
endfunction

function automatic logic [63:0] shifted_coeff(
    input logic [63:0] coeff,
    input logic [63:0] q,
    input logic        wrap
);
    begin
        if (wrap && (coeff != 64'd0)) begin
            shifted_coeff = q - coeff;
        end else begin
            shifted_coeff = coeff;
        end
    end
endfunction

assign out_valid = busy;
assign in_ready  = ~busy | output_consumed;

assign input_accepted  = in_valid & in_ready;
assign output_consumed = out_valid & out_ready;

always_ff @(posedge clk) begin
    logic [63:0] coeff0_v, coeff1_v, coeff2_v, coeff3_v;
    logic [63:0] coeff4_v, coeff5_v, coeff6_v, coeff7_v;
    logic [63:0] res0, res1, res2, res3, res4, res5, res6, res7;
    logic [3:0]  idx0, idx1, idx2, idx3, idx4, idx5, idx6, idx7;
    logic        wrap0, wrap1, wrap2, wrap3, wrap4, wrap5, wrap6, wrap7;

    if (rst) begin
        busy   <= 1'b0;
        out_c0 <= 64'd0;
        out_c1 <= 64'd0;
        out_c2 <= 64'd0;
        out_c3 <= 64'd0;
        out_c4 <= 64'd0;
        out_c5 <= 64'd0;
        out_c6 <= 64'd0;
        out_c7 <= 64'd0;
    end else if (input_accepted) begin
        idx0 = {1'b0, in_shift} + 4'd0;
        idx1 = {1'b0, in_shift} + 4'd1;
        idx2 = {1'b0, in_shift} + 4'd2;
        idx3 = {1'b0, in_shift} + 4'd3;
        idx4 = {1'b0, in_shift} + 4'd4;
        idx5 = {1'b0, in_shift} + 4'd5;
        idx6 = {1'b0, in_shift} + 4'd6;
        idx7 = {1'b0, in_shift} + 4'd7;

        wrap0 = (idx0 >= 4'd8);
        wrap1 = (idx1 >= 4'd8);
        wrap2 = (idx2 >= 4'd8);
        wrap3 = (idx3 >= 4'd8);
        wrap4 = (idx4 >= 4'd8);
        wrap5 = (idx5 >= 4'd8);
        wrap6 = (idx6 >= 4'd8);
        wrap7 = (idx7 >= 4'd8);

        coeff0_v = shifted_coeff(in_c0, in_modulus, wrap0);
        coeff1_v = shifted_coeff(in_c1, in_modulus, wrap1);
        coeff2_v = shifted_coeff(in_c2, in_modulus, wrap2);
        coeff3_v = shifted_coeff(in_c3, in_modulus, wrap3);
        coeff4_v = shifted_coeff(in_c4, in_modulus, wrap4);
        coeff5_v = shifted_coeff(in_c5, in_modulus, wrap5);
        coeff6_v = shifted_coeff(in_c6, in_modulus, wrap6);
        coeff7_v = shifted_coeff(in_c7, in_modulus, wrap7);

        res0 = 64'd0;
        res1 = 64'd0;
        res2 = 64'd0;
        res3 = 64'd0;
        res4 = 64'd0;
        res5 = 64'd0;
        res6 = 64'd0;
        res7 = 64'd0;

        unique case (idx0[2:0])
            3'd0: res0 = coeff0_v;
            3'd1: res1 = coeff0_v;
            3'd2: res2 = coeff0_v;
            3'd3: res3 = coeff0_v;
            3'd4: res4 = coeff0_v;
            3'd5: res5 = coeff0_v;
            3'd6: res6 = coeff0_v;
            3'd7: res7 = coeff0_v;
        endcase

        unique case (idx1[2:0])
            3'd0: res0 = coeff1_v;
            3'd1: res1 = coeff1_v;
            3'd2: res2 = coeff1_v;
            3'd3: res3 = coeff1_v;
            3'd4: res4 = coeff1_v;
            3'd5: res5 = coeff1_v;
            3'd6: res6 = coeff1_v;
            3'd7: res7 = coeff1_v;
        endcase

        unique case (idx2[2:0])
            3'd0: res0 = coeff2_v;
            3'd1: res1 = coeff2_v;
            3'd2: res2 = coeff2_v;
            3'd3: res3 = coeff2_v;
            3'd4: res4 = coeff2_v;
            3'd5: res5 = coeff2_v;
            3'd6: res6 = coeff2_v;
            3'd7: res7 = coeff2_v;
        endcase

        unique case (idx3[2:0])
            3'd0: res0 = coeff3_v;
            3'd1: res1 = coeff3_v;
            3'd2: res2 = coeff3_v;
            3'd3: res3 = coeff3_v;
            3'd4: res4 = coeff3_v;
            3'd5: res5 = coeff3_v;
            3'd6: res6 = coeff3_v;
            3'd7: res7 = coeff3_v;
        endcase

        unique case (idx4[2:0])
            3'd0: res0 = coeff4_v;
            3'd1: res1 = coeff4_v;
            3'd2: res2 = coeff4_v;
            3'd3: res3 = coeff4_v;
            3'd4: res4 = coeff4_v;
            3'd5: res5 = coeff4_v;
            3'd6: res6 = coeff4_v;
            3'd7: res7 = coeff4_v;
        endcase

        unique case (idx5[2:0])
            3'd0: res0 = coeff5_v;
            3'd1: res1 = coeff5_v;
            3'd2: res2 = coeff5_v;
            3'd3: res3 = coeff5_v;
            3'd4: res4 = coeff5_v;
            3'd5: res5 = coeff5_v;
            3'd6: res6 = coeff5_v;
            3'd7: res7 = coeff5_v;
        endcase

        unique case (idx6[2:0])
            3'd0: res0 = coeff6_v;
            3'd1: res1 = coeff6_v;
            3'd2: res2 = coeff6_v;
            3'd3: res3 = coeff6_v;
            3'd4: res4 = coeff6_v;
            3'd5: res5 = coeff6_v;
            3'd6: res6 = coeff6_v;
            3'd7: res7 = coeff6_v;
        endcase

        unique case (idx7[2:0])
            3'd0: res0 = coeff7_v;
            3'd1: res1 = coeff7_v;
            3'd2: res2 = coeff7_v;
            3'd3: res3 = coeff7_v;
            3'd4: res4 = coeff7_v;
            3'd5: res5 = coeff7_v;
            3'd6: res6 = coeff7_v;
            3'd7: res7 = coeff7_v;
        endcase

        out_c0 <= res0;
        out_c1 <= res1;
        out_c2 <= res2;
        out_c3 <= res3;
        out_c4 <= res4;
        out_c5 <= res5;
        out_c6 <= res6;
        out_c7 <= res7;
        busy   <= 1'b1;
    end else if (output_consumed) begin
        busy <= 1'b0;
    end
end

endmodule