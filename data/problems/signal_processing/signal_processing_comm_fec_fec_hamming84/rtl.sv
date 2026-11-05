module signal_processing_comm_fec_fec_hamming84 (
    input  logic        clk,
    input  logic        rst,

    input  logic        in_valid,
    output logic        in_ready,
    input  logic        in_encode,
    input  logic [7:0]  in_data0,
    input  logic [7:0]  in_data1,

    output logic        out_valid,
    input  logic        out_ready,
    output logic [7:0]  out_data
);

    logic        busy;
    logic [1:0]  beats_total;
    logic [1:0]  beats_sent;
    logic [7:0]  out_buf0;
    logic [7:0]  out_buf1;

    logic [7:0]  next_enc_hi;
    logic [7:0]  next_enc_lo;
    logic [3:0]  next_dec_hi;
    logic [3:0]  next_dec_lo;
    logic [7:0]  next_dec_byte;

    wire input_accepted;
    wire output_consumed;
    wire frame_complete;

    assign input_accepted  = in_valid & in_ready;
    assign output_consumed = out_valid & out_ready;
    assign frame_complete  = output_consumed & (beats_sent == (beats_total - 2'd1));

    assign out_valid = busy;
    assign in_ready  = (~busy) | frame_complete;
    assign out_data  = (beats_sent == 2'd0) ? out_buf0 : out_buf1;

    always_comb begin
        next_enc_hi   = enc_lookup(in_data0[7:4]);
        next_enc_lo   = enc_lookup(in_data0[3:0]);
        next_dec_hi   = dec_lookup(in_data0);
        next_dec_lo   = dec_lookup(in_data1);
        next_dec_byte = {next_dec_hi, next_dec_lo};
    end

    always_ff @(posedge clk) begin
        if (rst) begin
            busy       <= 1'b0;
            beats_total<= 2'd0;
            beats_sent <= 2'd0;
            out_buf0   <= 8'h00;
            out_buf1   <= 8'h00;
        end else if (input_accepted) begin
            busy      <= 1'b1;
            beats_sent<= 2'd0;
            if (in_encode) begin
                beats_total <= 2'd2;
                out_buf0    <= next_enc_hi;
                out_buf1    <= next_enc_lo;
            end else begin
                beats_total <= 2'd1;
                out_buf0    <= next_dec_byte;
                out_buf1    <= 8'h00;
            end
        end else if (output_consumed) begin
            if (beats_sent == (beats_total - 2'd1)) begin
                busy       <= 1'b0;
                beats_total<= 2'd0;
                beats_sent <= 2'd0;
            end else begin
                busy       <= 1'b1;
                beats_sent <= beats_sent + 2'd1;
            end
        end
    end

    function automatic logic [7:0] enc_lookup(input logic [3:0] nibble);
        begin
            unique case (nibble)
                4'h0: enc_lookup = 8'h00;
                4'h1: enc_lookup = 8'hD2;
                4'h2: enc_lookup = 8'h55;
                4'h3: enc_lookup = 8'h87;
                4'h4: enc_lookup = 8'h99;
                4'h5: enc_lookup = 8'h4B;
                4'h6: enc_lookup = 8'hCC;
                4'h7: enc_lookup = 8'h1E;
                4'h8: enc_lookup = 8'hE1;
                4'h9: enc_lookup = 8'h33;
                4'hA: enc_lookup = 8'hB4;
                4'hB: enc_lookup = 8'h66;
                4'hC: enc_lookup = 8'h78;
                4'hD: enc_lookup = 8'hAA;
                4'hE: enc_lookup = 8'h2D;
                4'hF: enc_lookup = 8'hFF;
            endcase
        end
    endfunction

    function automatic logic [3:0] dec_lookup(input logic [7:0] codeword);
        begin
            unique case (codeword)
                8'h00: dec_lookup = 4'h0;
                8'h01: dec_lookup = 4'h0;
                8'h02: dec_lookup = 4'h0;
                8'h03: dec_lookup = 4'h0;
                8'h04: dec_lookup = 4'h0;
                8'h05: dec_lookup = 4'h0;
                8'h06: dec_lookup = 4'h3;
                8'h07: dec_lookup = 4'h3;
                8'h08: dec_lookup = 4'h0;
                8'h09: dec_lookup = 4'h0;
                8'h0A: dec_lookup = 4'h5;
                8'h0B: dec_lookup = 4'h5;
                8'h0C: dec_lookup = 4'hE;
                8'h0D: dec_lookup = 4'hE;
                8'h0E: dec_lookup = 4'h7;
                8'h0F: dec_lookup = 4'h7;
                8'h10: dec_lookup = 4'h0;
                8'h11: dec_lookup = 4'h0;
                8'h12: dec_lookup = 4'h9;
                8'h13: dec_lookup = 4'h9;
                8'h14: dec_lookup = 4'h2;
                8'h15: dec_lookup = 4'h2;
                8'h16: dec_lookup = 4'h7;
                8'h17: dec_lookup = 4'h7;
                8'h18: dec_lookup = 4'h4;
                8'h19: dec_lookup = 4'h4;
                8'h1A: dec_lookup = 4'h7;
                8'h1B: dec_lookup = 4'h7;
                8'h1C: dec_lookup = 4'h7;
                8'h1D: dec_lookup = 4'h7;
                8'h1E: dec_lookup = 4'h7;
                8'h1F: dec_lookup = 4'h7;
                8'h20: dec_lookup = 4'h0;
                8'h21: dec_lookup = 4'h0;
                8'h22: dec_lookup = 4'h9;
                8'h23: dec_lookup = 4'h9;
                8'h24: dec_lookup = 4'hE;
                8'h25: dec_lookup = 4'hE;
                8'h26: dec_lookup = 4'hB;
                8'h27: dec_lookup = 4'hB;
                8'h28: dec_lookup = 4'hE;
                8'h29: dec_lookup = 4'hE;
                8'h2A: dec_lookup = 4'hD;
                8'h2B: dec_lookup = 4'hD;
                8'h2C: dec_lookup = 4'hE;
                8'h2D: dec_lookup = 4'hE;
                8'h2E: dec_lookup = 4'hE;
                8'h2F: dec_lookup = 4'hE;
                8'h30: dec_lookup = 4'h9;
                8'h31: dec_lookup = 4'h9;
                8'h32: dec_lookup = 4'h9;
                8'h33: dec_lookup = 4'h9;
                8'h34: dec_lookup = 4'hA;
                8'h35: dec_lookup = 4'hA;
                8'h36: dec_lookup = 4'h9;
                8'h37: dec_lookup = 4'h9;
                8'h38: dec_lookup = 4'hC;
                8'h39: dec_lookup = 4'hC;
                8'h3A: dec_lookup = 4'h9;
                8'h3B: dec_lookup = 4'h9;
                8'h3C: dec_lookup = 4'hE;
                8'h3D: dec_lookup = 4'hE;
                8'h3E: dec_lookup = 4'h7;
                8'h3F: dec_lookup = 4'h7;
                8'h40: dec_lookup = 4'h0;
                8'h41: dec_lookup = 4'h0;
                8'h42: dec_lookup = 4'h5;
                8'h43: dec_lookup = 4'h5;
                8'h44: dec_lookup = 4'h2;
                8'h45: dec_lookup = 4'h2;
                8'h46: dec_lookup = 4'hB;
                8'h47: dec_lookup = 4'hB;
                8'h48: dec_lookup = 4'h5;
                8'h49: dec_lookup = 4'h5;
                8'h4A: dec_lookup = 4'h5;
                8'h4B: dec_lookup = 4'h5;
                8'h4C: dec_lookup = 4'h6;
                8'h4D: dec_lookup = 4'h6;
                8'h4E: dec_lookup = 4'h5;
                8'h4F: dec_lookup = 4'h5;
                8'h50: dec_lookup = 4'h2;
                8'h51: dec_lookup = 4'h2;
                8'h52: dec_lookup = 4'h1;
                8'h53: dec_lookup = 4'h1;
                8'h54: dec_lookup = 4'h2;
                8'h55: dec_lookup = 4'h2;
                8'h56: dec_lookup = 4'h2;
                8'h57: dec_lookup = 4'h2;
                8'h58: dec_lookup = 4'hC;
                8'h59: dec_lookup = 4'hC;
                8'h5A: dec_lookup = 4'h5;
                8'h5B: dec_lookup = 4'h5;
                8'h5C: dec_lookup = 4'h2;
                8'h5D: dec_lookup = 4'h2;
                8'h5E: dec_lookup = 4'h7;
                8'h5F: dec_lookup = 4'h7;
                8'h60: dec_lookup = 4'h8;
                8'h61: dec_lookup = 4'h8;
                8'h62: dec_lookup = 4'hB;
                8'h63: dec_lookup = 4'hB;
                8'h64: dec_lookup = 4'hB;
                8'h65: dec_lookup = 4'hB;
                8'h66: dec_lookup = 4'hB;
                8'h67: dec_lookup = 4'hB;
                8'h68: dec_lookup = 4'hC;
                8'h69: dec_lookup = 4'hC;
                8'h6A: dec_lookup = 4'h5;
                8'h6B: dec_lookup = 4'h5;
                8'h6C: dec_lookup = 4'hE;
                8'h6D: dec_lookup = 4'hE;
                8'h6E: dec_lookup = 4'hB;
                8'h6F: dec_lookup = 4'hB;
                8'h70: dec_lookup = 4'hC;
                8'h71: dec_lookup = 4'hC;
                8'h72: dec_lookup = 4'h9;
                8'h73: dec_lookup = 4'h9;
                8'h74: dec_lookup = 4'h2;
                8'h75: dec_lookup = 4'h2;
                8'h76: dec_lookup = 4'hB;
                8'h77: dec_lookup = 4'hB;
                8'h78: dec_lookup = 4'hC;
                8'h79: dec_lookup = 4'hC;
                8'h7A: dec_lookup = 4'hC;
                8'h7B: dec_lookup = 4'hC;
                8'h7C: dec_lookup = 4'hC;
                8'h7D: dec_lookup = 4'hC;
                8'h7E: dec_lookup = 4'hF;
                8'h7F: dec_lookup = 4'hF;
                8'h80: dec_lookup = 4'h0;
                8'h81: dec_lookup = 4'h0;
                8'h82: dec_lookup = 4'h3;
                8'h83: dec_lookup = 4'h3;
                8'h84: dec_lookup = 4'h3;
                8'h85: dec_lookup = 4'h3;
                8'h86: dec_lookup = 4'h3;
                8'h87: dec_lookup = 4'h3;
                8'h88: dec_lookup = 4'h4;
                8'h89: dec_lookup = 4'h4;
                8'h8A: dec_lookup = 4'hD;
                8'h8B: dec_lookup = 4'hD;
                8'h8C: dec_lookup = 4'h6;
                8'h8D: dec_lookup = 4'h6;
                8'h8E: dec_lookup = 4'h3;
                8'h8F: dec_lookup = 4'h3;
                8'h90: dec_lookup = 4'h4;
                8'h91: dec_lookup = 4'h4;
                8'h92: dec_lookup = 4'h1;
                8'h93: dec_lookup = 4'h1;
                8'h94: dec_lookup = 4'hA;
                8'h95: dec_lookup = 4'hA;
                8'h96: dec_lookup = 4'h3;
                8'h97: dec_lookup = 4'h3;
                8'h98: dec_lookup = 4'h4;
                8'h99: dec_lookup = 4'h4;
                8'h9A: dec_lookup = 4'h4;
                8'h9B: dec_lookup = 4'h4;
                8'h9C: dec_lookup = 4'h4;
                8'h9D: dec_lookup = 4'h4;
                8'h9E: dec_lookup = 4'h7;
                8'h9F: dec_lookup = 4'h7;
                8'hA0: dec_lookup = 4'h8;
                8'hA1: dec_lookup = 4'h8;
                8'hA2: dec_lookup = 4'hD;
                8'hA3: dec_lookup = 4'hD;
                8'hA4: dec_lookup = 4'hA;
                8'hA5: dec_lookup = 4'hA;
                8'hA6: dec_lookup = 4'h3;
                8'hA7: dec_lookup = 4'h3;
                8'hA8: dec_lookup = 4'hD;
                8'hA9: dec_lookup = 4'hD;
                8'hAA: dec_lookup = 4'hD;
                8'hAB: dec_lookup = 4'hD;
                8'hAC: dec_lookup = 4'hE;
                8'hAD: dec_lookup = 4'hE;
                8'hAE: dec_lookup = 4'hD;
                8'hAF: dec_lookup = 4'hD;
                8'hB0: dec_lookup = 4'hA;
                8'hB1: dec_lookup = 4'hA;
                8'hB2: dec_lookup = 4'h9;
                8'hB3: dec_lookup = 4'h9;
                8'hB4: dec_lookup = 4'hA;
                8'hB5: dec_lookup = 4'hA;
                8'hB6: dec_lookup = 4'hA;
                8'hB7: dec_lookup = 4'hA;
                8'hB8: dec_lookup = 4'h4;
                8'hB9: dec_lookup = 4'h4;
                8'hBA: dec_lookup = 4'hD;
                8'hBB: dec_lookup = 4'hD;
                8'hBC: dec_lookup = 4'hA;
                8'hBD: dec_lookup = 4'hA;
                8'hBE: dec_lookup = 4'hF;
                8'hBF: dec_lookup = 4'hF;
                8'hC0: dec_lookup = 4'h8;
                8'hC1: dec_lookup = 4'h8;
                8'hC2: dec_lookup = 4'h1;
                8'hC3: dec_lookup = 4'h1;
                8'hC4: dec_lookup = 4'h6;
                8'hC5: dec_lookup = 4'h6;
                8'hC6: dec_lookup = 4'h3;
                8'hC7: dec_lookup = 4'h3;
                8'hC8: dec_lookup = 4'h6;
                8'hC9: dec_lookup = 4'h6;
                8'hCA: dec_lookup = 4'h5;
                8'hCB: dec_lookup = 4'h5;
                8'hCC: dec_lookup = 4'h6;
                8'hCD: dec_lookup = 4'h6;
                8'hCE: dec_lookup = 4'h6;
                8'hCF: dec_lookup = 4'h6;
                8'hD0: dec_lookup = 4'h1;
                8'hD1: dec_lookup = 4'h1;
                8'hD2: dec_lookup = 4'h1;
                8'hD3: dec_lookup = 4'h1;
                8'hD4: dec_lookup = 4'h2;
                8'hD5: dec_lookup = 4'h2;
                8'hD6: dec_lookup = 4'h1;
                8'hD7: dec_lookup = 4'h1;
                8'hD8: dec_lookup = 4'h4;
                8'hD9: dec_lookup = 4'h4;
                8'hDA: dec_lookup = 4'h1;
                8'hDB: dec_lookup = 4'h1;
                8'hDC: dec_lookup = 4'h6;
                8'hDD: dec_lookup = 4'h6;
                8'hDE: dec_lookup = 4'hF;
                8'hDF: dec_lookup = 4'hF;
                8'hE0: dec_lookup = 4'h8;
                8'hE1: dec_lookup = 4'h8;
                8'hE2: dec_lookup = 4'h8;
                8'hE3: dec_lookup = 4'h8;
                8'hE4: dec_lookup = 4'h8;
                8'hE5: dec_lookup = 4'h8;
                8'hE6: dec_lookup = 4'hB;
                8'hE7: dec_lookup = 4'hB;
                8'hE8: dec_lookup = 4'h8;
                8'hE9: dec_lookup = 4'h8;
                8'hEA: dec_lookup = 4'hD;
                8'hEB: dec_lookup = 4'hD;
                8'hEC: dec_lookup = 4'h6;
                8'hED: dec_lookup = 4'h6;
                8'hEE: dec_lookup = 4'hF;
                8'hEF: dec_lookup = 4'hF;
                8'hF0: dec_lookup = 4'h8;
                8'hF1: dec_lookup = 4'h8;
                8'hF2: dec_lookup = 4'h1;
                8'hF3: dec_lookup = 4'h1;
                8'hF4: dec_lookup = 4'hA;
                8'hF5: dec_lookup = 4'hA;
                8'hF6: dec_lookup = 4'hF;
                8'hF7: dec_lookup = 4'hF;
                8'hF8: dec_lookup = 4'hC;
                8'hF9: dec_lookup = 4'hC;
                8'hFA: dec_lookup = 4'hF;
                8'hFB: dec_lookup = 4'hF;
                8'hFC: dec_lookup = 4'hF;
                8'hFD: dec_lookup = 4'hF;
                8'hFE: dec_lookup = 4'hF;
                8'hFF: dec_lookup = 4'hF;
            endcase
        end
    endfunction

endmodule