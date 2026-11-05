module IntToRecFN (
    input  logic        clk,
    input  logic        rst,

    input  logic        in_valid,
    output logic        in_ready,
    input  logic        in_signedIn,
    input  logic [31:0] in_in,
    input  logic [2:0]  in_roundingMode,
    input  logic        in_detectTininess,

    output logic        out_valid,
    input  logic        out_ready,
    output logic [32:0] out_out,
    output logic [4:0]  out_exceptionFlags
);

    logic busy;

    wire input_accepted;
    wire output_consumed;

    logic        sign_c;
    logic [31:0] magnitude_c;
    logic [5:0]  p_c;
    logic [23:0] sig_c;
    logic [5:0]  p_rounded_c;
    logic        inexact_c;
    logic [7:0]  exp_ieee_c;
    logic [8:0]  rec_exp_c;
    logic [22:0] frac_c;
    logic [32:0] out_out_c;
    logic [4:0]  out_exceptionFlags_c;

    assign input_accepted  = in_valid & in_ready;
    assign output_consumed = out_valid & out_ready;

    assign in_ready  = ~busy | output_consumed;
    assign out_valid = busy;

    function automatic [5:0] msb_index32(input logic [31:0] value);
        begin
            msb_index32 = 6'd0;
            if (value[31])      msb_index32 = 6'd31;
            else if (value[30]) msb_index32 = 6'd30;
            else if (value[29]) msb_index32 = 6'd29;
            else if (value[28]) msb_index32 = 6'd28;
            else if (value[27]) msb_index32 = 6'd27;
            else if (value[26]) msb_index32 = 6'd26;
            else if (value[25]) msb_index32 = 6'd25;
            else if (value[24]) msb_index32 = 6'd24;
            else if (value[23]) msb_index32 = 6'd23;
            else if (value[22]) msb_index32 = 6'd22;
            else if (value[21]) msb_index32 = 6'd21;
            else if (value[20]) msb_index32 = 6'd20;
            else if (value[19]) msb_index32 = 6'd19;
            else if (value[18]) msb_index32 = 6'd18;
            else if (value[17]) msb_index32 = 6'd17;
            else if (value[16]) msb_index32 = 6'd16;
            else if (value[15]) msb_index32 = 6'd15;
            else if (value[14]) msb_index32 = 6'd14;
            else if (value[13]) msb_index32 = 6'd13;
            else if (value[12]) msb_index32 = 6'd12;
            else if (value[11]) msb_index32 = 6'd11;
            else if (value[10]) msb_index32 = 6'd10;
            else if (value[9])  msb_index32 = 6'd9;
            else if (value[8])  msb_index32 = 6'd8;
            else if (value[7])  msb_index32 = 6'd7;
            else if (value[6])  msb_index32 = 6'd6;
            else if (value[5])  msb_index32 = 6'd5;
            else if (value[4])  msb_index32 = 6'd4;
            else if (value[3])  msb_index32 = 6'd3;
            else if (value[2])  msb_index32 = 6'd2;
            else if (value[1])  msb_index32 = 6'd1;
            else if (value[0])  msb_index32 = 6'd0;
        end
    endfunction

    always_comb begin
        sign_c               = 1'b0;
        magnitude_c          = 32'd0;
        p_c                  = 6'd0;
        sig_c                = 24'd0;
        p_rounded_c          = 6'd0;
        inexact_c            = 1'b0;
        exp_ieee_c           = 8'd0;
        rec_exp_c            = 9'd0;
        frac_c               = 23'd0;
        out_out_c            = 33'd0;
        out_exceptionFlags_c = 5'd0;

        if (in_signedIn && in_in[31]) begin
            sign_c      = 1'b1;
            magnitude_c = (~in_in) + 32'd1;
        end else begin
            sign_c      = 1'b0;
            magnitude_c = in_in;
        end

        if (magnitude_c == 32'd0) begin
            out_out_c            = 33'd0;
            out_exceptionFlags_c = 5'd0;
        end else begin
            logic [5:0]  shift;
            logic [23:0] trunc;
            logic [31:0] remainder;
            logic [31:0] trunc_shifted;
            logic [31:0] half;
            logic        greater_than_half;
            logic        exactly_half;
            logic [2:0]  rm;
            logic        increment;
            logic [24:0] sig_pre;

            shift             = 6'd0;
            trunc             = 24'd0;
            remainder         = 32'd0;
            trunc_shifted     = 32'd0;
            half              = 32'd0;
            greater_than_half = 1'b0;
            exactly_half      = 1'b0;
            rm                = 3'd0;
            increment         = 1'b0;
            sig_pre           = 25'd0;

            p_c = msb_index32(magnitude_c);

            if (p_c <= 6'd23) begin
                sig_c       = magnitude_c << (6'd23 - p_c);
                inexact_c   = 1'b0;
                p_rounded_c = p_c;
            end else begin
                shift         = p_c - 6'd23;
                trunc         = magnitude_c >> shift;
                trunc_shifted = trunc << shift;
                remainder     = magnitude_c - trunc_shifted;
                half          = 32'd1 << (shift - 6'd1);

                greater_than_half = (remainder > half);
                exactly_half      = (remainder == half);
                inexact_c         = (remainder != 32'd0);

                rm = in_roundingMode;
                if (rm == 3'd5 || rm == 3'd7) begin
                    rm = 3'd0;
                end

                unique case (rm)
                    3'd0: increment = greater_than_half | (exactly_half & trunc[0]);
                    3'd1: increment = 1'b0;
                    3'd2: increment = sign_c & inexact_c;
                    3'd3: increment = (~sign_c) & inexact_c;
                    3'd4: increment = greater_than_half | exactly_half;
                    default: increment = 1'b0;
                endcase

                sig_pre = {1'b0, trunc} + {{24{1'b0}}, increment};
                if (rm == 3'd6 && inexact_c)
                    sig_pre = {1'b0, trunc} | 25'd1;

                if (sig_pre == 25'd16777216) begin
                    sig_c       = 24'd8388608;
                    p_rounded_c = p_c + 6'd1;
                end else begin
                    sig_c       = sig_pre[23:0];
                    p_rounded_c = p_c;
                end
            end

            exp_ieee_c           = p_rounded_c + 8'd127;
            frac_c               = sig_c - 24'd8388608;
            rec_exp_c            = exp_ieee_c + 9'd129;
            out_out_c            = {sign_c, rec_exp_c, frac_c};
            out_exceptionFlags_c = {4'b0000, inexact_c};
        end
    end

    always_ff @(posedge clk) begin
        if (rst) begin
            busy               <= 1'b0;
            out_out            <= 33'b0;
            out_exceptionFlags <= 5'b00000;
        end else if (input_accepted) begin
            busy               <= 1'b1;
            out_out            <= out_out_c;
            out_exceptionFlags <= out_exceptionFlags_c;
        end else if (output_consumed) begin
            busy <= 1'b0;
        end
    end

endmodule
