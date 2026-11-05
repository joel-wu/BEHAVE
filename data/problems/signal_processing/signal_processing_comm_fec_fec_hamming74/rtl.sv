module hamming74_codec (
    input  logic        clk,
    input  logic        rst,

    input  logic        in_valid,
    output logic        in_ready,
    input  logic [1:0]  in_mode,
    input  logic [7:0]  in_data,
    input  logic [6:0]  in_code0,
    input  logic [6:0]  in_code1,
    input  logic [7:0]  in_soft0,
    input  logic [7:0]  in_soft1,
    input  logic [7:0]  in_soft2,
    input  logic [7:0]  in_soft3,
    input  logic [7:0]  in_soft4,
    input  logic [7:0]  in_soft5,
    input  logic [7:0]  in_soft6,
    input  logic [7:0]  in_soft7,
    input  logic [7:0]  in_soft8,
    input  logic [7:0]  in_soft9,
    input  logic [7:0]  in_soft10,
    input  logic [7:0]  in_soft11,
    input  logic [7:0]  in_soft12,
    input  logic [7:0]  in_soft13,

    output logic        out_valid,
    input  logic        out_ready,
    output logic [7:0]  out_data,
    output logic [6:0]  out_code0,
    output logic [6:0]  out_code1
);

    logic busy;
    logic [7:0] out_data_next;
    logic [6:0] out_code0_next;
    logic [6:0] out_code1_next;

    wire input_accepted;
    wire output_consumed;

    assign input_accepted  = in_valid & in_ready;
    assign output_consumed = out_valid & out_ready;

    assign in_ready  = ~busy | output_consumed;
    assign out_valid = busy;

    function automatic logic [6:0] enc_table(input logic [3:0] nibble);
        begin
            unique case (nibble)
                4'h0: enc_table = 7'h00;
                4'h1: enc_table = 7'h69;
                4'h2: enc_table = 7'h2A;
                4'h3: enc_table = 7'h43;
                4'h4: enc_table = 7'h4C;
                4'h5: enc_table = 7'h25;
                4'h6: enc_table = 7'h66;
                4'h7: enc_table = 7'h0F;
                4'h8: enc_table = 7'h70;
                4'h9: enc_table = 7'h19;
                4'hA: enc_table = 7'h5A;
                4'hB: enc_table = 7'h33;
                4'hC: enc_table = 7'h3C;
                4'hD: enc_table = 7'h55;
                4'hE: enc_table = 7'h16;
                4'hF: enc_table = 7'h7F;
            endcase
        end
    endfunction

    function automatic logic [3:0] dec_table(input logic [6:0] code);
        begin
            unique case (code)
                7'h00: dec_table = 4'h0; 7'h01: dec_table = 4'h0; 7'h02: dec_table = 4'h0; 7'h03: dec_table = 4'h3;
                7'h04: dec_table = 4'h0; 7'h05: dec_table = 4'h5; 7'h06: dec_table = 4'hE; 7'h07: dec_table = 4'h7;
                7'h08: dec_table = 4'h0; 7'h09: dec_table = 4'h9; 7'h0A: dec_table = 4'h2; 7'h0B: dec_table = 4'h7;
                7'h0C: dec_table = 4'h4; 7'h0D: dec_table = 4'h7; 7'h0E: dec_table = 4'h7; 7'h0F: dec_table = 4'h7;
                7'h10: dec_table = 4'h0; 7'h11: dec_table = 4'h9; 7'h12: dec_table = 4'hE; 7'h13: dec_table = 4'hB;
                7'h14: dec_table = 4'hE; 7'h15: dec_table = 4'hD; 7'h16: dec_table = 4'hE; 7'h17: dec_table = 4'hE;
                7'h18: dec_table = 4'h9; 7'h19: dec_table = 4'h9; 7'h1A: dec_table = 4'hA; 7'h1B: dec_table = 4'h9;
                7'h1C: dec_table = 4'hC; 7'h1D: dec_table = 4'h9; 7'h1E: dec_table = 4'hE; 7'h1F: dec_table = 4'h7;
                7'h20: dec_table = 4'h0; 7'h21: dec_table = 4'h5; 7'h22: dec_table = 4'h2; 7'h23: dec_table = 4'hB;
                7'h24: dec_table = 4'h5; 7'h25: dec_table = 4'h5; 7'h26: dec_table = 4'h6; 7'h27: dec_table = 4'h5;
                7'h28: dec_table = 4'h2; 7'h29: dec_table = 4'h1; 7'h2A: dec_table = 4'h2; 7'h2B: dec_table = 4'h2;
                7'h2C: dec_table = 4'hC; 7'h2D: dec_table = 4'h5; 7'h2E: dec_table = 4'h2; 7'h2F: dec_table = 4'h7;
                7'h30: dec_table = 4'h8; 7'h31: dec_table = 4'hB; 7'h32: dec_table = 4'hB; 7'h33: dec_table = 4'hB;
                7'h34: dec_table = 4'hC; 7'h35: dec_table = 4'h5; 7'h36: dec_table = 4'hE; 7'h37: dec_table = 4'hB;
                7'h38: dec_table = 4'hC; 7'h39: dec_table = 4'h9; 7'h3A: dec_table = 4'h2; 7'h3B: dec_table = 4'hB;
                7'h3C: dec_table = 4'hC; 7'h3D: dec_table = 4'hC; 7'h3E: dec_table = 4'hC; 7'h3F: dec_table = 4'hF;
                7'h40: dec_table = 4'h0; 7'h41: dec_table = 4'h3; 7'h42: dec_table = 4'h3; 7'h43: dec_table = 4'h3;
                7'h44: dec_table = 4'h4; 7'h45: dec_table = 4'hD; 7'h46: dec_table = 4'h6; 7'h47: dec_table = 4'h3;
                7'h48: dec_table = 4'h4; 7'h49: dec_table = 4'h1; 7'h4A: dec_table = 4'hA; 7'h4B: dec_table = 4'h3;
                7'h4C: dec_table = 4'h4; 7'h4D: dec_table = 4'h4; 7'h4E: dec_table = 4'h4; 7'h4F: dec_table = 4'h7;
                7'h50: dec_table = 4'h8; 7'h51: dec_table = 4'hD; 7'h52: dec_table = 4'hA; 7'h53: dec_table = 4'h3;
                7'h54: dec_table = 4'hD; 7'h55: dec_table = 4'hD; 7'h56: dec_table = 4'hE; 7'h57: dec_table = 4'hD;
                7'h58: dec_table = 4'hA; 7'h59: dec_table = 4'h9; 7'h5A: dec_table = 4'hA; 7'h5B: dec_table = 4'hA;
                7'h5C: dec_table = 4'h4; 7'h5D: dec_table = 4'hD; 7'h5E: dec_table = 4'hA; 7'h5F: dec_table = 4'hF;
                7'h60: dec_table = 4'h8; 7'h61: dec_table = 4'h1; 7'h62: dec_table = 4'h6; 7'h63: dec_table = 4'h3;
                7'h64: dec_table = 4'h6; 7'h65: dec_table = 4'h5; 7'h66: dec_table = 4'h6; 7'h67: dec_table = 4'h6;
                7'h68: dec_table = 4'h1; 7'h69: dec_table = 4'h1; 7'h6A: dec_table = 4'h2; 7'h6B: dec_table = 4'h1;
                7'h6C: dec_table = 4'h4; 7'h6D: dec_table = 4'h1; 7'h6E: dec_table = 4'h6; 7'h6F: dec_table = 4'hF;
                7'h70: dec_table = 4'h8; 7'h71: dec_table = 4'h8; 7'h72: dec_table = 4'h8; 7'h73: dec_table = 4'hB;
                7'h74: dec_table = 4'h8; 7'h75: dec_table = 4'hD; 7'h76: dec_table = 4'h6; 7'h77: dec_table = 4'hF;
                7'h78: dec_table = 4'h8; 7'h79: dec_table = 4'h1; 7'h7A: dec_table = 4'hA; 7'h7B: dec_table = 4'hF;
                7'h7C: dec_table = 4'hC; 7'h7D: dec_table = 4'hF; 7'h7E: dec_table = 4'hF; 7'h7F: dec_table = 4'hF;
            endcase
        end
    endfunction

    function automatic logic [11:0] soft_distance(
        input logic [6:0] codeword,
        input logic [7:0] s0,
        input logic [7:0] s1,
        input logic [7:0] s2,
        input logic [7:0] s3,
        input logic [7:0] s4,
        input logic [7:0] s5,
        input logic [7:0] s6
    );
        logic [11:0] accum;
        begin
            accum = 12'd0;
            accum = accum + (codeword[6] ? ({4'b0, (8'hFF - s0)}) : ({4'b0, s0}));
            accum = accum + (codeword[5] ? ({4'b0, (8'hFF - s1)}) : ({4'b0, s1}));
            accum = accum + (codeword[4] ? ({4'b0, (8'hFF - s2)}) : ({4'b0, s2}));
            accum = accum + (codeword[3] ? ({4'b0, (8'hFF - s3)}) : ({4'b0, s3}));
            accum = accum + (codeword[2] ? ({4'b0, (8'hFF - s4)}) : ({4'b0, s4}));
            accum = accum + (codeword[1] ? ({4'b0, (8'hFF - s5)}) : ({4'b0, s5}));
            accum = accum + (codeword[0] ? ({4'b0, (8'hFF - s6)}) : ({4'b0, s6}));
            soft_distance = accum;
        end
    endfunction

    function automatic logic [3:0] soft_decode_symbol(
        input logic [7:0] s0,
        input logic [7:0] s1,
        input logic [7:0] s2,
        input logic [7:0] s3,
        input logic [7:0] s4,
        input logic [7:0] s5,
        input logic [7:0] s6
    );
        logic [3:0] best_s;
        logic [11:0] best_d;
        logic [11:0] cand_d;
        logic [6:0] cand_c;
        begin
            best_s = 4'd0;
            best_d = 12'd0;

            cand_c = enc_table(4'd0);  cand_d = soft_distance(cand_c, s0, s1, s2, s3, s4, s5, s6); best_s = 4'd0;  best_d = cand_d;
            cand_c = enc_table(4'd1);  cand_d = soft_distance(cand_c, s0, s1, s2, s3, s4, s5, s6); if (cand_d < best_d) begin best_s = 4'd1;  best_d = cand_d; end
            cand_c = enc_table(4'd2);  cand_d = soft_distance(cand_c, s0, s1, s2, s3, s4, s5, s6); if (cand_d < best_d) begin best_s = 4'd2;  best_d = cand_d; end
            cand_c = enc_table(4'd3);  cand_d = soft_distance(cand_c, s0, s1, s2, s3, s4, s5, s6); if (cand_d < best_d) begin best_s = 4'd3;  best_d = cand_d; end
            cand_c = enc_table(4'd4);  cand_d = soft_distance(cand_c, s0, s1, s2, s3, s4, s5, s6); if (cand_d < best_d) begin best_s = 4'd4;  best_d = cand_d; end
            cand_c = enc_table(4'd5);  cand_d = soft_distance(cand_c, s0, s1, s2, s3, s4, s5, s6); if (cand_d < best_d) begin best_s = 4'd5;  best_d = cand_d; end
            cand_c = enc_table(4'd6);  cand_d = soft_distance(cand_c, s0, s1, s2, s3, s4, s5, s6); if (cand_d < best_d) begin best_s = 4'd6;  best_d = cand_d; end
            cand_c = enc_table(4'd7);  cand_d = soft_distance(cand_c, s0, s1, s2, s3, s4, s5, s6); if (cand_d < best_d) begin best_s = 4'd7;  best_d = cand_d; end
            cand_c = enc_table(4'd8);  cand_d = soft_distance(cand_c, s0, s1, s2, s3, s4, s5, s6); if (cand_d < best_d) begin best_s = 4'd8;  best_d = cand_d; end
            cand_c = enc_table(4'd9);  cand_d = soft_distance(cand_c, s0, s1, s2, s3, s4, s5, s6); if (cand_d < best_d) begin best_s = 4'd9;  best_d = cand_d; end
            cand_c = enc_table(4'd10); cand_d = soft_distance(cand_c, s0, s1, s2, s3, s4, s5, s6); if (cand_d < best_d) begin best_s = 4'd10; best_d = cand_d; end
            cand_c = enc_table(4'd11); cand_d = soft_distance(cand_c, s0, s1, s2, s3, s4, s5, s6); if (cand_d < best_d) begin best_s = 4'd11; best_d = cand_d; end
            cand_c = enc_table(4'd12); cand_d = soft_distance(cand_c, s0, s1, s2, s3, s4, s5, s6); if (cand_d < best_d) begin best_s = 4'd12; best_d = cand_d; end
            cand_c = enc_table(4'd13); cand_d = soft_distance(cand_c, s0, s1, s2, s3, s4, s5, s6); if (cand_d < best_d) begin best_s = 4'd13; best_d = cand_d; end
            cand_c = enc_table(4'd14); cand_d = soft_distance(cand_c, s0, s1, s2, s3, s4, s5, s6); if (cand_d < best_d) begin best_s = 4'd14; best_d = cand_d; end
            cand_c = enc_table(4'd15); cand_d = soft_distance(cand_c, s0, s1, s2, s3, s4, s5, s6); if (cand_d < best_d) begin best_s = 4'd15; best_d = cand_d; end

            soft_decode_symbol = best_s;
        end
    endfunction

    logic [3:0] nibble0;
    logic [3:0] nibble1;
    logic [7:0] decoded_byte;

    always_comb begin
        out_data_next  = 8'h00;
        out_code0_next = 7'h00;
        out_code1_next = 7'h00;
        nibble0        = 4'h0;
        nibble1        = 4'h0;
        decoded_byte   = 8'h00;

        unique case (in_mode)
            2'b01: begin
                nibble0      = dec_table(in_code0);
                nibble1      = dec_table(in_code1);
                decoded_byte = {nibble0, nibble1};
                out_data_next = decoded_byte;
            end

            2'b10: begin
                nibble0      = soft_decode_symbol(in_soft0, in_soft1, in_soft2, in_soft3, in_soft4, in_soft5, in_soft6);
                nibble1      = soft_decode_symbol(in_soft7, in_soft8, in_soft9, in_soft10, in_soft11, in_soft12, in_soft13);
                decoded_byte = {nibble0, nibble1};
                out_data_next = decoded_byte;
            end

            2'b00: begin
                out_code0_next = enc_table(in_data[7:4]);
                out_code1_next = enc_table(in_data[3:0]);
            end

            2'b11: begin
                out_code0_next = enc_table(in_data[7:4]);
                out_code1_next = enc_table(in_data[3:0]);
            end
        endcase
    end

    always_ff @(posedge clk) begin
        if (rst) begin
            busy      <= 1'b0;
            out_data  <= 8'h00;
            out_code0 <= 7'h00;
            out_code1 <= 7'h00;
        end else if (input_accepted) begin
            busy      <= 1'b1;
            out_data  <= out_data_next;
            out_code0 <= out_code0_next;
            out_code1 <= out_code1_next;
        end else if (output_consumed) begin
            busy      <= 1'b0;
            out_data  <= 8'h00;
            out_code0 <= 7'h00;
            out_code1 <= 7'h00;
        end
    end

endmodule