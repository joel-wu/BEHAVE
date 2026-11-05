module md5_compression_round_pipeline (
    input  logic         clk,
    input  logic         rst,
    input  logic         in_valid,
    output logic         in_ready,
    input  logic [511:0] in_wb,
    input  logic [31:0]  in_a0,
    input  logic [31:0]  in_b0,
    input  logic [31:0]  in_c0,
    input  logic [31:0]  in_d0,
    output logic         out_valid,
    input  logic         out_ready,
    output logic [31:0]  out_a64,
    output logic [31:0]  out_b64,
    output logic [31:0]  out_c64,
    output logic [31:0]  out_d64
);

logic        busy;
logic [6:0]  step_count;
logic [511:0] wb_reg;
logic [31:0] a_reg;
logic [31:0] b_reg;
logic [31:0] c_reg;
logic [31:0] d_reg;

wire input_accepted;
wire output_consumed;
wire done;

logic [5:0] idx_cur;
logic [31:0] f_val;
logic [3:0]  g_idx;
logic [31:0] w_val;
logic [31:0] k_val;
logic [4:0]  s_val;
logic [31:0] t_val;
logic [31:0] r_val;
logic [31:0] next_a;
logic [31:0] next_b;
logic [31:0] next_c;
logic [31:0] next_d;

assign input_accepted  = in_valid & in_ready;
assign output_consumed = out_valid & out_ready;
assign done            = (step_count == 7'd64);

assign in_ready  = ~busy | output_consumed;
assign out_valid = busy & done;

function automatic logic [31:0] rol32(
    input logic [31:0] x,
    input logic [4:0]  s
);
    logic [31:0] left_part;
    logic [31:0] right_part;
    logic [5:0]  shamt6;
begin
    shamt6    = {1'b0, s};
    left_part = x << s;
    right_part = x >> (6'd32 - shamt6);
    rol32 = left_part | right_part;
end
endfunction

function automatic logic [31:0] md5_k(input logic [5:0] idx);
begin
    unique case (idx)
        6'd0:  md5_k = 32'hD76AA478;
        6'd1:  md5_k = 32'hE8C7B756;
        6'd2:  md5_k = 32'h242070DB;
        6'd3:  md5_k = 32'hC1BDCEEE;
        6'd4:  md5_k = 32'hF57C0FAF;
        6'd5:  md5_k = 32'h4787C62A;
        6'd6:  md5_k = 32'hA8304613;
        6'd7:  md5_k = 32'hFD469501;
        6'd8:  md5_k = 32'h698098D8;
        6'd9:  md5_k = 32'h8B44F7AF;
        6'd10: md5_k = 32'hFFFF5BB1;
        6'd11: md5_k = 32'h895CD7BE;
        6'd12: md5_k = 32'h6B901122;
        6'd13: md5_k = 32'hFD987193;
        6'd14: md5_k = 32'hA679438E;
        6'd15: md5_k = 32'h49B40821;
        6'd16: md5_k = 32'hF61E2562;
        6'd17: md5_k = 32'hC040B340;
        6'd18: md5_k = 32'h265E5A51;
        6'd19: md5_k = 32'hE9B6C7AA;
        6'd20: md5_k = 32'hD62F105D;
        6'd21: md5_k = 32'h02441453;
        6'd22: md5_k = 32'hD8A1E681;
        6'd23: md5_k = 32'hE7D3FBC8;
        6'd24: md5_k = 32'h21E1CDE6;
        6'd25: md5_k = 32'hC33707D6;
        6'd26: md5_k = 32'hF4D50D87;
        6'd27: md5_k = 32'h455A14ED;
        6'd28: md5_k = 32'hA9E3E905;
        6'd29: md5_k = 32'hFCEFA3F8;
        6'd30: md5_k = 32'h676F02D9;
        6'd31: md5_k = 32'h8D2A4C8A;
        6'd32: md5_k = 32'hFFFA3942;
        6'd33: md5_k = 32'h8771F681;
        6'd34: md5_k = 32'h6D9D6122;
        6'd35: md5_k = 32'hFDE5380C;
        6'd36: md5_k = 32'hA4BEEA44;
        6'd37: md5_k = 32'h4BDECFA9;
        6'd38: md5_k = 32'hF6BB4B60;
        6'd39: md5_k = 32'hBEBFBC70;
        6'd40: md5_k = 32'h289B7EC6;
        6'd41: md5_k = 32'hEAA127FA;
        6'd42: md5_k = 32'hD4EF3085;
        6'd43: md5_k = 32'h04881D05;
        6'd44: md5_k = 32'hD9D4D039;
        6'd45: md5_k = 32'hE6DB99E5;
        6'd46: md5_k = 32'h1FA27CF8;
        6'd47: md5_k = 32'hC4AC5665;
        6'd48: md5_k = 32'hF4292244;
        6'd49: md5_k = 32'h432AFF97;
        6'd50: md5_k = 32'hAB9423A7;
        6'd51: md5_k = 32'hFC93A039;
        6'd52: md5_k = 32'h655B59C3;
        6'd53: md5_k = 32'h8F0CCC92;
        6'd54: md5_k = 32'hFFEFF47D;
        6'd55: md5_k = 32'h85845DD1;
        6'd56: md5_k = 32'h6FA87E4F;
        6'd57: md5_k = 32'hFE2CE6E0;
        6'd58: md5_k = 32'hA3014314;
        6'd59: md5_k = 32'h4E0811A1;
        6'd60: md5_k = 32'hF7537E82;
        6'd61: md5_k = 32'hBD3AF235;
        6'd62: md5_k = 32'h2AD7D2BB;
        6'd63: md5_k = 32'hEB86D391;
    endcase
end
endfunction

function automatic logic [4:0] md5_s(input logic [5:0] idx);
begin
    unique case (idx)
        6'd0:  md5_s = 5'd7;
        6'd1:  md5_s = 5'd12;
        6'd2:  md5_s = 5'd17;
        6'd3:  md5_s = 5'd22;
        6'd4:  md5_s = 5'd7;
        6'd5:  md5_s = 5'd12;
        6'd6:  md5_s = 5'd17;
        6'd7:  md5_s = 5'd22;
        6'd8:  md5_s = 5'd7;
        6'd9:  md5_s = 5'd12;
        6'd10: md5_s = 5'd17;
        6'd11: md5_s = 5'd22;
        6'd12: md5_s = 5'd7;
        6'd13: md5_s = 5'd12;
        6'd14: md5_s = 5'd17;
        6'd15: md5_s = 5'd22;
        6'd16: md5_s = 5'd5;
        6'd17: md5_s = 5'd9;
        6'd18: md5_s = 5'd14;
        6'd19: md5_s = 5'd20;
        6'd20: md5_s = 5'd5;
        6'd21: md5_s = 5'd9;
        6'd22: md5_s = 5'd14;
        6'd23: md5_s = 5'd20;
        6'd24: md5_s = 5'd5;
        6'd25: md5_s = 5'd9;
        6'd26: md5_s = 5'd14;
        6'd27: md5_s = 5'd20;
        6'd28: md5_s = 5'd5;
        6'd29: md5_s = 5'd9;
        6'd30: md5_s = 5'd14;
        6'd31: md5_s = 5'd20;
        6'd32: md5_s = 5'd4;
        6'd33: md5_s = 5'd11;
        6'd34: md5_s = 5'd16;
        6'd35: md5_s = 5'd23;
        6'd36: md5_s = 5'd4;
        6'd37: md5_s = 5'd11;
        6'd38: md5_s = 5'd16;
        6'd39: md5_s = 5'd23;
        6'd40: md5_s = 5'd4;
        6'd41: md5_s = 5'd11;
        6'd42: md5_s = 5'd16;
        6'd43: md5_s = 5'd23;
        6'd44: md5_s = 5'd4;
        6'd45: md5_s = 5'd11;
        6'd46: md5_s = 5'd16;
        6'd47: md5_s = 5'd23;
        6'd48: md5_s = 5'd6;
        6'd49: md5_s = 5'd10;
        6'd50: md5_s = 5'd15;
        6'd51: md5_s = 5'd21;
        6'd52: md5_s = 5'd6;
        6'd53: md5_s = 5'd10;
        6'd54: md5_s = 5'd15;
        6'd55: md5_s = 5'd21;
        6'd56: md5_s = 5'd6;
        6'd57: md5_s = 5'd10;
        6'd58: md5_s = 5'd15;
        6'd59: md5_s = 5'd21;
        6'd60: md5_s = 5'd6;
        6'd61: md5_s = 5'd10;
        6'd62: md5_s = 5'd15;
        6'd63: md5_s = 5'd21;
    endcase
end
endfunction

function automatic logic [3:0] md5_g(input logic [5:0] idx);
begin
    unique case (idx)
        6'd0:  md5_g = 4'd0;
        6'd1:  md5_g = 4'd1;
        6'd2:  md5_g = 4'd2;
        6'd3:  md5_g = 4'd3;
        6'd4:  md5_g = 4'd4;
        6'd5:  md5_g = 4'd5;
        6'd6:  md5_g = 4'd6;
        6'd7:  md5_g = 4'd7;
        6'd8:  md5_g = 4'd8;
        6'd9:  md5_g = 4'd9;
        6'd10: md5_g = 4'd10;
        6'd11: md5_g = 4'd11;
        6'd12: md5_g = 4'd12;
        6'd13: md5_g = 4'd13;
        6'd14: md5_g = 4'd14;
        6'd15: md5_g = 4'd15;
        6'd16: md5_g = 4'd1;
        6'd17: md5_g = 4'd6;
        6'd18: md5_g = 4'd11;
        6'd19: md5_g = 4'd0;
        6'd20: md5_g = 4'd5;
        6'd21: md5_g = 4'd10;
        6'd22: md5_g = 4'd15;
        6'd23: md5_g = 4'd4;
        6'd24: md5_g = 4'd9;
        6'd25: md5_g = 4'd14;
        6'd26: md5_g = 4'd3;
        6'd27: md5_g = 4'd8;
        6'd28: md5_g = 4'd13;
        6'd29: md5_g = 4'd2;
        6'd30: md5_g = 4'd7;
        6'd31: md5_g = 4'd12;
        6'd32: md5_g = 4'd5;
        6'd33: md5_g = 4'd8;
        6'd34: md5_g = 4'd11;
        6'd35: md5_g = 4'd14;
        6'd36: md5_g = 4'd1;
        6'd37: md5_g = 4'd4;
        6'd38: md5_g = 4'd7;
        6'd39: md5_g = 4'd10;
        6'd40: md5_g = 4'd13;
        6'd41: md5_g = 4'd0;
        6'd42: md5_g = 4'd3;
        6'd43: md5_g = 4'd6;
        6'd44: md5_g = 4'd9;
        6'd45: md5_g = 4'd12;
        6'd46: md5_g = 4'd15;
        6'd47: md5_g = 4'd2;
        6'd48: md5_g = 4'd0;
        6'd49: md5_g = 4'd7;
        6'd50: md5_g = 4'd14;
        6'd51: md5_g = 4'd5;
        6'd52: md5_g = 4'd12;
        6'd53: md5_g = 4'd3;
        6'd54: md5_g = 4'd10;
        6'd55: md5_g = 4'd1;
        6'd56: md5_g = 4'd8;
        6'd57: md5_g = 4'd15;
        6'd58: md5_g = 4'd6;
        6'd59: md5_g = 4'd13;
        6'd60: md5_g = 4'd4;
        6'd61: md5_g = 4'd11;
        6'd62: md5_g = 4'd2;
        6'd63: md5_g = 4'd9;
    endcase
end
endfunction

function automatic logic [31:0] md5_word(
    input logic [511:0] wb,
    input logic [3:0] idx
);
begin
    unique case (idx)
        4'd0:  md5_word = wb[31:0];
        4'd1:  md5_word = wb[63:32];
        4'd2:  md5_word = wb[95:64];
        4'd3:  md5_word = wb[127:96];
        4'd4:  md5_word = wb[159:128];
        4'd5:  md5_word = wb[191:160];
        4'd6:  md5_word = wb[223:192];
        4'd7:  md5_word = wb[255:224];
        4'd8:  md5_word = wb[287:256];
        4'd9:  md5_word = wb[319:288];
        4'd10: md5_word = wb[351:320];
        4'd11: md5_word = wb[383:352];
        4'd12: md5_word = wb[415:384];
        4'd13: md5_word = wb[447:416];
        4'd14: md5_word = wb[479:448];
        4'd15: md5_word = wb[511:480];
    endcase
end
endfunction

always_comb begin
    idx_cur = step_count[5:0];

    if (idx_cur < 6'd16) begin
        f_val = (b_reg & c_reg) | ((~b_reg) & d_reg);
    end else if (idx_cur < 6'd32) begin
        f_val = (d_reg & b_reg) | ((~d_reg) & c_reg);
    end else if (idx_cur < 6'd48) begin
        f_val = b_reg ^ c_reg ^ d_reg;
    end else begin
        f_val = c_reg ^ (b_reg | (~d_reg));
    end

    g_idx  = md5_g(idx_cur);
    w_val  = md5_word(wb_reg, g_idx);
    k_val  = md5_k(idx_cur);
    s_val  = md5_s(idx_cur);
    t_val  = a_reg + f_val + k_val + w_val;
    r_val  = rol32(t_val, s_val);

    next_a = d_reg;
    next_b = b_reg + r_val;
    next_c = b_reg;
    next_d = c_reg;
end

always_ff @(posedge clk) begin
    if (rst) begin
        busy     <= 1'b0;
        step_count <= 7'd0;
        wb_reg   <= 512'd0;
        a_reg    <= 32'd0;
        b_reg    <= 32'd0;
        c_reg    <= 32'd0;
        d_reg    <= 32'd0;
        out_a64  <= 32'd0;
        out_b64  <= 32'd0;
        out_c64  <= 32'd0;
        out_d64  <= 32'd0;
    end else begin
        if (input_accepted) begin
            busy       <= 1'b1;
            step_count <= 7'd0;
            wb_reg     <= in_wb;
            a_reg      <= in_a0;
            b_reg      <= in_b0;
            c_reg      <= in_c0;
            d_reg      <= in_d0;
        end else if (busy) begin
            if (done) begin
                if (output_consumed) begin
                    busy       <= 1'b0;
                    step_count <= 7'd0;
                end
            end else begin
                a_reg      <= next_a;
                b_reg      <= next_b;
                c_reg      <= next_c;
                d_reg      <= next_d;
                step_count <= step_count + 7'd1;

                if (step_count == 7'd63) begin
                    out_a64 <= next_a;
                    out_b64 <= next_b;
                    out_c64 <= next_c;
                    out_d64 <= next_d;
                end
            end
        end
    end
end

endmodule