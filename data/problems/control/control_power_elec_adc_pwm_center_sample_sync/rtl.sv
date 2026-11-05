module AdcPwmSamplePlanner (
    input logic clk,
    input logic rst,
    input logic in_valid,
    output logic in_ready,
    input logic [31:0] in_top,
    input logic [31:0] in_duty,
    input logic [16:0] in_max_duty_uq16,
    input logic in_motor_type_dc,
    input logic in_pwm_mode_bipolar,
    input logic [2:0] in_comm_step,
    input logic in_direction,
    input logic in_is_detecting,
    input logic in_has_3_shunts,
    input logic in_curr1_double_sample,
    input logic in_curr2_double_sample,
    output logic out_valid,
    input logic out_ready,
    output logic [31:0] out_val_sample,
    output logic [31:0] out_curr1_sample,
    output logic [31:0] out_curr2_sample,
    output logic [31:0] out_curr3_sample,
    output logic [2:0] out_curr_samp_volt
);
    logic busy;
    logic [16:0] gain;
    logic [48:0] product;
    logic [31:0] limit_duty, duty, p, z, center, val, c1, c2, c3;
    logic [2:0] mask;
    logic double1, double2;
    assign in_ready = !rst && (!busy || out_ready);
    assign out_valid = !rst && busy;
    always_comb begin
        gain = in_max_duty_uq16 > 17'd65536 ? 17'd65536 : in_max_duty_uq16;
        product = {17'd0, in_top} * {32'd0, gain};
        limit_duty = product >> 16;
        duty = in_duty < limit_duty ? in_duty : limit_duty;
        p = duty + ((in_top - duty) >> 1);
        z = in_top - 32'd2;
        center = in_top - 32'd70;
        if (p < center) center = p;
        double1 = in_curr1_double_sample && (in_comm_step == 2 || in_comm_step == 3);
        double2 = in_curr2_double_sample && ((in_direction && (in_comm_step == 4 || in_comm_step == 5)) ||
                  (!in_direction && (in_comm_step == 1 || in_comm_step == 6)));
        val = 0; c1 = out_curr1_sample; c2 = out_curr2_sample; c3 = 0; mask = 0;
        if (in_motor_type_dc) begin
            c1 = in_top - 32'd10; c2 = c1;
            if (in_has_3_shunts) c3 = c1;
            if (duty > 1000) val = duty >> 1;
            else begin val = duty + 32'd800; mask = 3'd7; end
        end else if (in_is_detecting) begin
            val = duty >> 1; c1 = p; c2 = p;
            if (in_has_3_shunts) c3 = p;
        end else if (in_pwm_mode_bipolar) begin
            val = in_top >> 2;
            if (in_has_3_shunts) c3 = z;
            if (!in_direction) begin
                case (in_comm_step)
                    3'd1: begin c1 = z; c2 = p; mask = 3'd0; end
                    3'd2: begin c1 = p; c2 = z; mask = 3'd0; end
                    3'd3: begin c1 = p; c2 = z; mask = 3'd2; end
                    3'd4: begin c1 = z; c2 = z; mask = 3'd2; end
                    3'd5: begin c1 = z; c2 = z; mask = 3'd1; end
                    3'd6: begin c1 = z; c2 = p; mask = 3'd1; end
                    default: begin end
                endcase
            end else begin
                case (in_comm_step)
                    3'd1: begin c1 = z; c2 = z; mask = 3'd2; end
                    3'd2: begin c1 = p; c2 = z; mask = 3'd2; end
                    3'd3: begin c1 = p; c2 = z; mask = 3'd0; end
                    3'd4: begin c1 = z; c2 = p; mask = 3'd0; end
                    3'd5: begin c1 = z; c2 = p; mask = 3'd1; end
                    3'd6: begin c1 = z; c2 = z; mask = 3'd1; end
                    default: begin end
                endcase
            end
        end else begin
            val = duty >> 1; c1 = center; c2 = center;
            if (in_has_3_shunts) c3 = center;
            if (duty > (in_top >> 1)) begin
                if (double1 || double2) begin
                    c1 = duty + 32'd90; c2 = in_top - 32'd230;
                end
                if (!in_has_3_shunts) begin
                    if (!in_direction) begin
                        case (in_comm_step)
                            3'd1: mask = 3'd1;
                            3'd2: mask = 3'd2;
                            3'd3: mask = 3'd2;
                            3'd4: mask = 3'd3;
                            3'd5: mask = 3'd3;
                            3'd6: mask = 3'd1;
                            default: begin end
                        endcase
                    end else begin
                        case (in_comm_step)
                            3'd1: mask = 3'd3;
                            3'd2: mask = 3'd2;
                            3'd3: mask = 3'd2;
                            3'd4: mask = 3'd1;
                            3'd5: mask = 3'd1;
                            3'd6: mask = 3'd3;
                            default: begin end
                        endcase
                    end
                end else begin
                    if (!in_direction) begin
                        case (in_comm_step)
                            3'd1: mask = 3'd3;
                            3'd2: mask = 3'd6;
                            3'd3: mask = 3'd6;
                            3'd4: mask = 3'd5;
                            3'd5: mask = 3'd5;
                            3'd6: mask = 3'd3;
                            default: begin end
                        endcase
                    end else begin
                        case (in_comm_step)
                            3'd1: mask = 3'd5;
                            3'd2: mask = 3'd6;
                            3'd3: mask = 3'd6;
                            3'd4: mask = 3'd3;
                            3'd5: mask = 3'd3;
                            3'd6: mask = 3'd5;
                            default: begin end
                        endcase
                    end
                end
            end
        end
    end
    always_ff @(posedge clk or posedge rst) begin
        if (rst) begin
            busy <= 0; out_val_sample <= 0; out_curr1_sample <= 0;
            out_curr2_sample <= 0; out_curr3_sample <= 0; out_curr_samp_volt <= 0;
        end else if (in_valid && in_ready) begin
            busy <= 1; out_val_sample <= val; out_curr1_sample <= c1;
            out_curr2_sample <= c2; out_curr3_sample <= c3; out_curr_samp_volt <= mask;
        end else if (out_ready) busy <= 0;
    end
endmodule
