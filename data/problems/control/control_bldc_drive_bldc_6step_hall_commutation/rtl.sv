module control_bldc_drive_bldc_6step_hall_commutation (
    input  logic        clk,
    input  logic        rst,

    input  logic        in_valid,
    output logic        in_ready,
    input  logic [15:0] in_uq,
    input  logic [15:0] in_voltage_limit,
    input  logic [15:0] in_angle_el,
    input  logic        in_mode_150,
    input  logic        in_modulation_centered,

    output logic        out_valid,
    input  logic        out_ready,
    output logic [15:0] out_ua,
    output logic [15:0] out_ub,
    output logic [15:0] out_uc,
    output logic        out_phase_a_en,
    output logic        out_phase_b_en,
    output logic        out_phase_c_en
);

    logic busy;
    wire input_accepted;
    wire output_consumed;

    logic signed [15:0] uq_s;
    logic signed [15:0] voltage_limit_s;
    logic signed [16:0] center_s;
    logic [16:0] angle_off_u;
    logic [19:0] sector_mul_120;
    logic [20:0] sector_mul_150;
    logic [2:0] sector_120;
    logic [3:0] sector_150;

    logic [1:0] ma_sel;
    logic [1:0] mb_sel;
    logic [1:0] mc_sel;

    logic signed [17:0] va_raw;
    logic signed [17:0] vb_raw;
    logic signed [17:0] vc_raw;

    logic [15:0] ua_next;
    logic [15:0] ub_next;
    logic [15:0] uc_next;
    logic        phase_a_en_next;
    logic        phase_b_en_next;
    logic        phase_c_en_next;

    function automatic logic [15:0] sat16(input logic signed [17:0] value);
        begin
            if (value > 18'sd32767)
                sat16 = 16'h7fff;
            else if (value < -18'sd32768)
                sat16 = 16'h8000;
            else
                sat16 = value[15:0];
        end
    endfunction

    assign input_accepted  = in_valid & in_ready;
    assign output_consumed = out_valid & out_ready;

    assign in_ready  = ~busy | output_consumed;
    assign out_valid = busy;

    always_comb begin
        uq_s            = $signed(in_uq);
        voltage_limit_s = $signed(in_voltage_limit);
        center_s        = in_modulation_centered ? ($signed(in_voltage_limit) >>> 1) : $signed(in_uq);

        angle_off_u   = {1'b0, in_angle_el} + 17'h01555;
        sector_mul_120 = angle_off_u[15:0] * 6;
        sector_mul_150 = angle_off_u[15:0] * 12;
        sector_120     = sector_mul_120[18:16];
        sector_150     = sector_mul_150[19:16];

        ma_sel = 2'b00;
        mb_sel = 2'b00;
        mc_sel = 2'b00;

        if (in_mode_150) begin
            unique case (sector_150)
                4'd0:  begin ma_sel = 2'b00; mb_sel = 2'b01; mc_sel = 2'b10; end
                4'd1:  begin ma_sel = 2'b10; mb_sel = 2'b01; mc_sel = 2'b10; end
                4'd2:  begin ma_sel = 2'b10; mb_sel = 2'b01; mc_sel = 2'b00; end
                4'd3:  begin ma_sel = 2'b10; mb_sel = 2'b01; mc_sel = 2'b01; end
                4'd4:  begin ma_sel = 2'b10; mb_sel = 2'b00; mc_sel = 2'b01; end
                4'd5:  begin ma_sel = 2'b10; mb_sel = 2'b10; mc_sel = 2'b01; end
                4'd6:  begin ma_sel = 2'b00; mb_sel = 2'b10; mc_sel = 2'b01; end
                4'd7:  begin ma_sel = 2'b01; mb_sel = 2'b10; mc_sel = 2'b01; end
                4'd8:  begin ma_sel = 2'b01; mb_sel = 2'b10; mc_sel = 2'b00; end
                4'd9:  begin ma_sel = 2'b01; mb_sel = 2'b10; mc_sel = 2'b10; end
                4'd10: begin ma_sel = 2'b01; mb_sel = 2'b00; mc_sel = 2'b10; end
                4'd11: begin ma_sel = 2'b01; mb_sel = 2'b01; mc_sel = 2'b10; end
            endcase
        end else begin
            unique case (sector_120)
                3'd0: begin ma_sel = 2'b00; mb_sel = 2'b01; mc_sel = 2'b10; end
                3'd1: begin ma_sel = 2'b10; mb_sel = 2'b01; mc_sel = 2'b00; end
                3'd2: begin ma_sel = 2'b10; mb_sel = 2'b00; mc_sel = 2'b01; end
                3'd3: begin ma_sel = 2'b00; mb_sel = 2'b10; mc_sel = 2'b01; end
                3'd4: begin ma_sel = 2'b01; mb_sel = 2'b10; mc_sel = 2'b00; end
                3'd5: begin ma_sel = 2'b01; mb_sel = 2'b00; mc_sel = 2'b10; end
            endcase
        end

        va_raw = center_s;
        vb_raw = center_s;
        vc_raw = center_s;

        if (ma_sel == 2'b01)
            va_raw = center_s + uq_s;
        else if (ma_sel == 2'b10)
            va_raw = center_s - uq_s;

        if (mb_sel == 2'b01)
            vb_raw = center_s + uq_s;
        else if (mb_sel == 2'b10)
            vb_raw = center_s - uq_s;

        if (mc_sel == 2'b01)
            vc_raw = center_s + uq_s;
        else if (mc_sel == 2'b10)
            vc_raw = center_s - uq_s;

        ua_next = sat16(va_raw);
        ub_next = sat16(vb_raw);
        uc_next = sat16(vc_raw);

        phase_a_en_next = (ma_sel != 2'b00);
        phase_b_en_next = (mb_sel != 2'b00);
        phase_c_en_next = (mc_sel != 2'b00);
    end

    always_ff @(posedge clk) begin
        if (rst) begin
            busy           <= 1'b0;
            out_ua         <= 16'h0000;
            out_ub         <= 16'h0000;
            out_uc         <= 16'h0000;
            out_phase_a_en <= 1'b0;
            out_phase_b_en <= 1'b0;
            out_phase_c_en <= 1'b0;
        end else if (input_accepted) begin
            busy           <= 1'b1;
            out_ua         <= ua_next;
            out_ub         <= ub_next;
            out_uc         <= uc_next;
            out_phase_a_en <= phase_a_en_next;
            out_phase_b_en <= phase_b_en_next;
            out_phase_c_en <= phase_c_en_next;
        end else if (output_consumed) begin
            busy <= 1'b0;
        end
    end

endmodule