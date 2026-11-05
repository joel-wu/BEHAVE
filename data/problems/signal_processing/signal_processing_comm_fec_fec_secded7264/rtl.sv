module signal_processing_comm_fec_fec_secded7264 (
    input  logic        clk,
    input  logic        rst,

    input  logic        in_valid,
    output logic        in_ready,
    input  logic        in_mode,
    input  logic [63:0] in_data,
    input  logic [71:0] in_codeword,

    output logic        out_valid,
    input  logic        out_ready,
    output logic [71:0] out_codeword,
    output logic [63:0] out_data,
    output logic [1:0]  out_status
);

    logic busy;
    wire input_accepted;
    wire output_consumed;

    assign input_accepted  = in_valid & in_ready;
    assign output_consumed = out_valid & out_ready;
    assign in_ready        = ~busy | output_consumed;
    assign out_valid       = busy;

    function automatic [3:0] popcount8(input logic [7:0] x);
        begin
            popcount8 =
                {3'b000, x[0]} +
                {3'b000, x[1]} +
                {3'b000, x[2]} +
                {3'b000, x[3]} +
                {3'b000, x[4]} +
                {3'b000, x[5]} +
                {3'b000, x[6]} +
                {3'b000, x[7]};
        end
    endfunction

    function automatic logic [7:0] p_val(input logic [5:0] idx);
        begin
            p_val = 8'h00;
            case (idx)
                6'd0:  p_val = 8'hFF;
                6'd1:  p_val = 8'h0F;
                6'd2:  p_val = 8'h0F;
                6'd3:  p_val = 8'h0C;
                6'd4:  p_val = 8'h68;
                6'd5:  p_val = 8'h88;
                6'd6:  p_val = 8'h88;
                6'd7:  p_val = 8'h80;
                6'd8:  p_val = 8'hF0;
                6'd9:  p_val = 8'hFF;
                6'd10: p_val = 8'h00;
                6'd11: p_val = 8'hF3;
                6'd12: p_val = 8'h64;
                6'd13: p_val = 8'h44;
                6'd14: p_val = 8'h44;
                6'd15: p_val = 8'h40;
                6'd16: p_val = 8'h30;
                6'd17: p_val = 8'hF0;
                6'd18: p_val = 8'hFF;
                6'd19: p_val = 8'h0F;
                6'd20: p_val = 8'h02;
                6'd21: p_val = 8'h22;
                6'd22: p_val = 8'h22;
                6'd23: p_val = 8'h26;
                6'd24: p_val = 8'hCF;
                6'd25: p_val = 8'h00;
                6'd26: p_val = 8'hF0;
                6'd27: p_val = 8'hFF;
                6'd28: p_val = 8'h01;
                6'd29: p_val = 8'h11;
                6'd30: p_val = 8'h11;
                6'd31: p_val = 8'h16;
                6'd32: p_val = 8'h68;
                6'd33: p_val = 8'h88;
                6'd34: p_val = 8'h88;
                6'd35: p_val = 8'h80;
                6'd36: p_val = 8'hFF;
                6'd37: p_val = 8'h0F;
                6'd38: p_val = 8'h00;
                6'd39: p_val = 8'hF3;
                6'd40: p_val = 8'h64;
                6'd41: p_val = 8'h44;
                6'd42: p_val = 8'h44;
                6'd43: p_val = 8'h40;
                6'd44: p_val = 8'hF0;
                6'd45: p_val = 8'hFF;
                6'd46: p_val = 8'h0F;
                6'd47: p_val = 8'h0C;
                6'd48: p_val = 8'h02;
                6'd49: p_val = 8'h22;
                6'd50: p_val = 8'h22;
                6'd51: p_val = 8'h26;
                6'd52: p_val = 8'hCF;
                6'd53: p_val = 8'h00;
                6'd54: p_val = 8'hFF;
                6'd55: p_val = 8'h0F;
                6'd56: p_val = 8'h01;
                6'd57: p_val = 8'h11;
                6'd58: p_val = 8'h11;
                6'd59: p_val = 8'h16;
                6'd60: p_val = 8'h30;
                6'd61: p_val = 8'hF0;
                6'd62: p_val = 8'hF0;
                6'd63: p_val = 8'hFF;
            endcase
        end
    endfunction

    function automatic logic [7:0] syndrome_w1_val(input logic [6:0] idx);
        begin
            syndrome_w1_val = 8'h00;
            case (idx)
                7'd0:  syndrome_w1_val = 8'h0B;
                7'd1:  syndrome_w1_val = 8'h3B;
                7'd2:  syndrome_w1_val = 8'h37;
                7'd3:  syndrome_w1_val = 8'h07;
                7'd4:  syndrome_w1_val = 8'h19;
                7'd5:  syndrome_w1_val = 8'h29;
                7'd6:  syndrome_w1_val = 8'h49;
                7'd7:  syndrome_w1_val = 8'h89;
                7'd8:  syndrome_w1_val = 8'h16;
                7'd9:  syndrome_w1_val = 8'h26;
                7'd10: syndrome_w1_val = 8'h46;
                7'd11: syndrome_w1_val = 8'h86;
                7'd12: syndrome_w1_val = 8'h13;
                7'd13: syndrome_w1_val = 8'h23;
                7'd14: syndrome_w1_val = 8'h43;
                7'd15: syndrome_w1_val = 8'h83;
                7'd16: syndrome_w1_val = 8'h1C;
                7'd17: syndrome_w1_val = 8'h2C;
                7'd18: syndrome_w1_val = 8'h4C;
                7'd19: syndrome_w1_val = 8'h8C;
                7'd20: syndrome_w1_val = 8'h15;
                7'd21: syndrome_w1_val = 8'h25;
                7'd22: syndrome_w1_val = 8'h45;
                7'd23: syndrome_w1_val = 8'h85;
                7'd24: syndrome_w1_val = 8'h1A;
                7'd25: syndrome_w1_val = 8'h2A;
                7'd26: syndrome_w1_val = 8'h4A;
                7'd27: syndrome_w1_val = 8'h8A;
                7'd28: syndrome_w1_val = 8'h0D;
                7'd29: syndrome_w1_val = 8'hCD;
                7'd30: syndrome_w1_val = 8'hCE;
                7'd31: syndrome_w1_val = 8'h0E;
                7'd32: syndrome_w1_val = 8'h70;
                7'd33: syndrome_w1_val = 8'h73;
                7'd34: syndrome_w1_val = 8'hB3;
                7'd35: syndrome_w1_val = 8'hB0;
                7'd36: syndrome_w1_val = 8'h51;
                7'd37: syndrome_w1_val = 8'h52;
                7'd38: syndrome_w1_val = 8'h54;
                7'd39: syndrome_w1_val = 8'h58;
                7'd40: syndrome_w1_val = 8'hA1;
                7'd41: syndrome_w1_val = 8'hA2;
                7'd42: syndrome_w1_val = 8'hA4;
                7'd43: syndrome_w1_val = 8'hA8;
                7'd44: syndrome_w1_val = 8'h31;
                7'd45: syndrome_w1_val = 8'h32;
                7'd46: syndrome_w1_val = 8'h34;
                7'd47: syndrome_w1_val = 8'h38;
                7'd48: syndrome_w1_val = 8'hC1;
                7'd49: syndrome_w1_val = 8'hC2;
                7'd50: syndrome_w1_val = 8'hC4;
                7'd51: syndrome_w1_val = 8'hC8;
                7'd52: syndrome_w1_val = 8'h61;
                7'd53: syndrome_w1_val = 8'h62;
                7'd54: syndrome_w1_val = 8'h64;
                7'd55: syndrome_w1_val = 8'h68;
                7'd56: syndrome_w1_val = 8'h91;
                7'd57: syndrome_w1_val = 8'h92;
                7'd58: syndrome_w1_val = 8'h94;
                7'd59: syndrome_w1_val = 8'h98;
                7'd60: syndrome_w1_val = 8'hE0;
                7'd61: syndrome_w1_val = 8'hEC;
                7'd62: syndrome_w1_val = 8'hDC;
                7'd63: syndrome_w1_val = 8'hD0;
                7'd64: syndrome_w1_val = 8'h01;
                7'd65: syndrome_w1_val = 8'h02;
                7'd66: syndrome_w1_val = 8'h04;
                7'd67: syndrome_w1_val = 8'h08;
                7'd68: syndrome_w1_val = 8'h10;
                7'd69: syndrome_w1_val = 8'h20;
                7'd70: syndrome_w1_val = 8'h40;
                7'd71: syndrome_w1_val = 8'h80;
            endcase
        end
    endfunction

    integer i;
    integer n;
    logic [7:0] d0, d1, d2, d3, d4, d5, d6, d7;
    logic [7:0] r0, r1, r2, r3, r4, r5, r6, r7, r8;
    logic [7:0] p;
    logic [7:0] s;
    logic [5:0] t_sum;
    logic [3:0] ws;
    logic       found;
    logic [6:0] n_match;
    logic [7:0] e0, e1, e2, e3, e4, e5, e6, e7, e8;
    logic [7:0] p_corr, c0, c1, c2, c3, c4, c5, c6, c7;
    logic [2:0] rem;
    logic [3:0] quot;
    logic [3:0] byte_index;

    always_ff @(posedge clk or posedge rst) begin
        if (rst) begin
            busy         <= 1'b0;
            out_codeword <= 72'h000000000000000000;
            out_data     <= 64'h0000000000000000;
            out_status   <= 2'b00;
        end else if (input_accepted) begin
            if (in_mode == 1'b0) begin
                d0 = in_data[63:56];
                d1 = in_data[55:48];
                d2 = in_data[47:40];
                d3 = in_data[39:32];
                d4 = in_data[31:24];
                d5 = in_data[23:16];
                d6 = in_data[15:8];
                d7 = in_data[7:0];

                p = 8'h00;
                for (i = 0; i < 8; i = i + 1) begin
                    t_sum =
                        popcount8(p_val(i*8+0) & d0) +
                        popcount8(p_val(i*8+1) & d1) +
                        popcount8(p_val(i*8+2) & d2) +
                        popcount8(p_val(i*8+3) & d3) +
                        popcount8(p_val(i*8+4) & d4) +
                        popcount8(p_val(i*8+5) & d5) +
                        popcount8(p_val(i*8+6) & d6) +
                        popcount8(p_val(i*8+7) & d7);
                    p = {p[6:0], t_sum[0]};
                end

                out_codeword <= {p, in_data};
                out_data     <= in_data;
                out_status   <= 2'b00;
            end else begin
                r0 = in_codeword[71:64];
                r1 = in_codeword[63:56];
                r2 = in_codeword[55:48];
                r3 = in_codeword[47:40];
                r4 = in_codeword[39:32];
                r5 = in_codeword[31:24];
                r6 = in_codeword[23:16];
                r7 = in_codeword[15:8];
                r8 = in_codeword[7:0];

                s = 8'h00;
                for (i = 0; i < 8; i = i + 1) begin
                    t_sum =
                        {5'b00000, r0[7-i]} +
                        popcount8(p_val(i*8+0) & r1) +
                        popcount8(p_val(i*8+1) & r2) +
                        popcount8(p_val(i*8+2) & r3) +
                        popcount8(p_val(i*8+3) & r4) +
                        popcount8(p_val(i*8+4) & r5) +
                        popcount8(p_val(i*8+5) & r6) +
                        popcount8(p_val(i*8+6) & r7) +
                        popcount8(p_val(i*8+7) & r8);
                    s = {s[6:0], t_sum[0]};
                end

                ws = popcount8(s);

                p_corr = r0;
                c0 = r1;
                c1 = r2;
                c2 = r3;
                c3 = r4;
                c4 = r5;
                c5 = r6;
                c6 = r7;
                c7 = r8;
                found = 1'b0;
                n_match = 7'd0;

                if (ws == 4'd0) begin
                    out_status <= 2'b00;
                end else begin
                    for (n = 0; n < 72; n = n + 1) begin
                        if ((found == 1'b0) && (s == syndrome_w1_val(n[6:0]))) begin
                            found = 1'b1;
                            n_match = n[6:0];
                        end
                    end

                    if (found) begin
                        e0 = 8'h00;
                        e1 = 8'h00;
                        e2 = 8'h00;
                        e3 = 8'h00;
                        e4 = 8'h00;
                        e5 = 8'h00;
                        e6 = 8'h00;
                        e7 = 8'h00;
                        e8 = 8'h00;

                        quot = {1'b0, n_match[6:3]};
                        rem  = n_match[2:0];
                        byte_index = 4'd8 - quot;

                        case (byte_index)
                            4'd0: e0 = (8'h01 << rem);
                            4'd1: e1 = (8'h01 << rem);
                            4'd2: e2 = (8'h01 << rem);
                            4'd3: e3 = (8'h01 << rem);
                            4'd4: e4 = (8'h01 << rem);
                            4'd5: e5 = (8'h01 << rem);
                            4'd6: e6 = (8'h01 << rem);
                            4'd7: e7 = (8'h01 << rem);
                            4'd8: e8 = (8'h01 << rem);
                            default: begin
                                e0 = 8'h00;
                                e1 = 8'h00;
                                e2 = 8'h00;
                                e3 = 8'h00;
                                e4 = 8'h00;
                                e5 = 8'h00;
                                e6 = 8'h00;
                                e7 = 8'h00;
                                e8 = 8'h00;
                            end
                        endcase

                        p_corr = r0 ^ e0;
                        c0 = r1 ^ e1;
                        c1 = r2 ^ e2;
                        c2 = r3 ^ e3;
                        c3 = r4 ^ e4;
                        c4 = r5 ^ e5;
                        c5 = r6 ^ e6;
                        c6 = r7 ^ e7;
                        c7 = r8 ^ e8;
                        out_status <= 2'b01;
                    end else begin
                        out_status <= 2'b10;
                    end
                end

                out_data     <= {c0, c1, c2, c3, c4, c5, c6, c7};
                out_codeword <= {p_corr, c0, c1, c2, c3, c4, c5, c6, c7};
            end

            busy <= 1'b1;
        end else if (output_consumed) begin
            busy <= 1'b0;
        end
    end

endmodule