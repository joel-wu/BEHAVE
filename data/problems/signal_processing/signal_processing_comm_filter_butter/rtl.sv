module signal_processing_comm_filter_butter (
    input  logic        clk,
    input  logic        rst,

    input  logic        in_valid,
    output logic        in_ready,
    input  logic [31:0] in_n,

    output logic        out_valid,
    input  logic        out_ready,
    output logic [1:0]  out_kind,
    output logic [31:0] out_real,
    output logic [31:0] out_imag
);

    localparam logic [31:0] GAIN_FP32 = 32'h3F800000;
    localparam logic [31:0] ZERO_FP32 = 32'h00000000;

    logic        busy;
    logic [4:0]  cur_n;
    logic [4:0]  out_idx;
    logic [4:0]  total_beats;

    logic [1:0]  cur_kind;
    logic [31:0] cur_real;
    logic [31:0] cur_imag;

    wire output_consumed;
    wire frame_complete;
    wire input_accepted;

    function automatic logic [4:0] clamp_n(input logic [31:0] n_in);
        begin
            if (n_in > 32'd16) clamp_n = 5'd16;
            else               clamp_n = n_in[4:0];
        end
    endfunction

    function automatic logic [63:0] pole_bits(input logic [4:0] n, input logic [4:0] idx);
        logic [63:0] pair_bits;
        begin
            pair_bits = '0;
            if (n[0] && idx == n - 1) begin
                pair_bits = 64'hBF80000000000000;
            end else begin
                case (n)
                    5'd2: case (idx >> 1)
                        5'd0: pair_bits = 64'hBF3504F33F3504F3;
                        default: pair_bits = '0;
                    endcase
                    5'd3: case (idx >> 1)
                        5'd0: pair_bits = 64'hBF0000003F5DB3D7;
                        default: pair_bits = '0;
                    endcase
                    5'd4: case (idx >> 1)
                        5'd0: pair_bits = 64'hBEC3EF153F6C835E;
                        5'd1: pair_bits = 64'hBF6C835E3EC3EF15;
                        default: pair_bits = '0;
                    endcase
                    5'd5: case (idx >> 1)
                        5'd0: pair_bits = 64'hBE9E377A3F737871;
                        5'd1: pair_bits = 64'hBF4F1BBD3F167918;
                        default: pair_bits = '0;
                    endcase
                    5'd6: case (idx >> 1)
                        5'd0: pair_bits = 64'hBE8483EE3F7746EA;
                        5'd1: pair_bits = 64'hBF3504F33F3504F3;
                        5'd2: pair_bits = 64'hBF7746EA3E8483EE;
                        default: pair_bits = '0;
                    endcase
                    5'd7: case (idx >> 1)
                        5'd0: pair_bits = 64'hBE63DC873F7994E0;
                        5'd1: pair_bits = 64'hBF1F9D073F48261C;
                        5'd2: pair_bits = 64'hBF66A5E53EDE2602;
                        default: pair_bits = '0;
                    endcase
                    5'd8: case (idx >> 1)
                        5'd0: pair_bits = 64'hBE47C5C23F7B14BE;
                        5'd1: pair_bits = 64'hBF0E39DA3F54DB31;
                        5'd2: pair_bits = 64'hBF54DB313F0E39DA;
                        5'd3: pair_bits = 64'hBF7B14BE3E47C5C2;
                        default: pair_bits = '0;
                    endcase
                    5'd9: case (idx >> 1)
                        5'd0: pair_bits = 64'hBE31D0D43F7C1C5C;
                        5'd1: pair_bits = 64'hBF0000003F5DB3D7;
                        5'd2: pair_bits = 64'hBF441B7D3F248DBB;
                        5'd3: pair_bits = 64'hBF708FB23EAF1D44;
                        default: pair_bits = '0;
                    endcase
                    5'd10: case (idx >> 1)
                        5'd0: pair_bits = 64'hBE20305B3F7CD925;
                        5'd1: pair_bits = 64'hBEE871713F641901;
                        5'd2: pair_bits = 64'hBF3504F33F3504F3;
                        5'd3: pair_bits = 64'hBF6419013EE87171;
                        5'd4: pair_bits = 64'hBF7CD9253E20305B;
                        default: pair_bits = '0;
                    endcase
                    5'd11: case (idx >> 1)
                        5'd0: pair_bits = 64'hBE11BAFB3F7D64F0;
                        5'd1: pair_bits = 64'hBED4B1473F68DDA4;
                        5'd2: pair_bits = 64'hBF27A4F43F4178CE;
                        5'd3: pair_bits = 64'hBF575C643F0A6770;
                        5'd4: pair_bits = 64'hBF75A1553E903F40;
                        default: pair_bits = '0;
                    endcase
                    5'd12: case (idx >> 1)
                        5'd0: pair_bits = 64'hBE05A8A83F7DCF55;
                        5'd1: pair_bits = 64'hBEC3EF153F6C835E;
                        5'd2: pair_bits = 64'hBF1BD7CA3F4B1934;
                        5'd3: pair_bits = 64'hBF4B19343F1BD7CA;
                        5'd4: pair_bits = 64'hBF6C835E3EC3EF15;
                        5'd5: pair_bits = 64'hBF7DCF553E05A8A8;
                        default: pair_bits = '0;
                    endcase
                    5'd13: case (idx >> 1)
                        5'd0: pair_bits = 64'hBDF6DBEF3F7E222B;
                        5'd1: pair_bits = 64'hBEB58EC63F6F5D39;
                        5'd2: pair_bits = 64'hBF116CB13F52AF12;
                        5'd3: pair_bits = 64'hBF3F9E673F29C268;
                        5'd4: pair_bits = 64'hBF62AD3F3EEDF032;
                        5'd5: pair_bits = 64'hBF788FA53E750F2A;
                        default: pair_bits = '0;
                    endcase
                    5'd14: case (idx >> 1)
                        5'd0: pair_bits = 64'hBDE54DA23F7E63EC;
                        5'd1: pair_bits = 64'hBEA91A563F71A257;
                        5'd2: pair_bits = 64'hBF0833413F58C2EB;
                        5'd3: pair_bits = 64'hBF3504F33F3504F3;
                        5'd4: pair_bits = 64'hBF58C2EB3F083341;
                        5'd5: pair_bits = 64'hBF71A2573EA91A56;
                        5'd6: pair_bits = 64'hBF7E63EC3DE54DA2;
                        default: pair_bits = '0;
                    endcase
                    5'd15: case (idx >> 1)
                        5'd0: pair_bits = 64'hBDD613053F7E98FD;
                        5'd1: pair_bits = 64'hBE9E377A3F737871;
                        5'd2: pair_bits = 64'hBF0000003F5DB3D7;
                        5'd3: pair_bits = 64'hBF2B4C253F3E3EBD;
                        5'd4: pair_bits = 64'hBF4F1BBD3F167918;
                        5'd5: pair_bits = 64'hBF69DE1D3ED03FC9;
                        5'd6: pair_bits = 64'hBF7A67E23E54E6CD;
                        default: pair_bits = '0;
                    endcase
                    5'd16: case (idx >> 1)
                        5'd0: pair_bits = 64'hBDC8BD363F7EC46D;
                        5'd1: pair_bits = 64'hBE94A0313F74FA0B;
                        5'd2: pair_bits = 64'hBEF15AEA3F61C598;
                        5'd3: pair_bits = 64'hBF2267993F45E403;
                        5'd4: pair_bits = 64'hBF45E4033F226799;
                        5'd5: pair_bits = 64'hBF61C5983EF15AEA;
                        5'd6: pair_bits = 64'hBF74FA0B3E94A031;
                        5'd7: pair_bits = 64'hBF7EC46D3DC8BD36;
                        default: pair_bits = '0;
                    endcase
                    default: pair_bits = '0;
                endcase
                if (idx[0]) pair_bits[31] = ~pair_bits[31];
            end
            pole_bits = pair_bits;
        end
    endfunction

    function automatic logic [31:0] pole_real(input logic [4:0] n, input logic [4:0] idx);
        logic [63:0] bits;
        begin
            bits = pole_bits(n, idx);
            pole_real = bits[63:32];
        end
    endfunction

    function automatic logic [31:0] pole_imag(input logic [4:0] n, input logic [4:0] idx);
        logic [63:0] bits;
        begin
            bits = pole_bits(n, idx);
            pole_imag = bits[31:0];
        end
    endfunction

    function automatic logic [1:0] beat_kind(input logic [4:0] n, input logic [4:0] idx);
        begin
            if (idx < n) beat_kind = 2'b00;
            else         beat_kind = 2'b01;
        end
    endfunction

    function automatic logic [31:0] beat_real(input logic [4:0] n, input logic [4:0] idx);
        begin
            if (idx < n) beat_real = pole_real(n, idx);
            else         beat_real = GAIN_FP32;
        end
    endfunction

    function automatic logic [31:0] beat_imag(input logic [4:0] n, input logic [4:0] idx);
        begin
            if (idx < n) beat_imag = pole_imag(n, idx);
            else         beat_imag = ZERO_FP32;
        end
    endfunction

    assign output_consumed = out_valid & out_ready;
    assign frame_complete  = output_consumed & (out_idx == (total_beats - 5'd1));
    assign input_accepted  = in_valid & in_ready;

    assign in_ready  = (~busy) | frame_complete;
    assign out_valid = busy;

    always_comb begin
        if (busy) begin
            out_kind = cur_kind;
            out_real = cur_real;
            out_imag = cur_imag;
        end else begin
            out_kind = 2'b00;
            out_real = 32'h00000000;
            out_imag = 32'h00000000;
        end
    end

    always_ff @(posedge clk or posedge rst) begin
        logic [4:0] n_eff;
        logic [4:0] first_total;
        logic [4:0] next_idx;
        begin
            if (rst) begin
                busy       <= 1'b0;
                cur_n      <= 5'd0;
                out_idx    <= 5'd0;
                total_beats<= 5'd0;
                cur_kind   <= 2'b00;
                cur_real   <= 32'h00000000;
                cur_imag   <= 32'h00000000;
            end else if (input_accepted) begin
                n_eff      = clamp_n(in_n);
                first_total= n_eff + 5'd1;

                busy        <= 1'b1;
                cur_n       <= n_eff;
                out_idx     <= 5'd0;
                total_beats <= first_total;
                cur_kind    <= beat_kind(n_eff, 5'd0);
                cur_real    <= beat_real(n_eff, 5'd0);
                cur_imag    <= beat_imag(n_eff, 5'd0);
            end else if (output_consumed) begin
                if (out_idx == (total_beats - 5'd1)) begin
                    busy        <= 1'b0;
                    cur_n       <= 5'd0;
                    out_idx     <= 5'd0;
                    total_beats <= 5'd0;
                    cur_kind    <= 2'b00;
                    cur_real    <= 32'h00000000;
                    cur_imag    <= 32'h00000000;
                end else begin
                    next_idx    = out_idx + 5'd1;
                    busy        <= 1'b1;
                    cur_n       <= cur_n;
                    out_idx     <= next_idx;
                    total_beats <= total_beats;
                    cur_kind    <= beat_kind(cur_n, next_idx);
                    cur_real    <= beat_real(cur_n, next_idx);
                    cur_imag    <= beat_imag(cur_n, next_idx);
                end
            end
        end
    end

endmodule
