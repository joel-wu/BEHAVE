module signal_processing_diffusion_sched_sched_k_dpm_2_ancestral_discrete (
    input logic clk,
    input logic rst,
    input logic in_valid,
    output logic in_ready,
    input logic in_phase,
    input logic [31:0] in_model_output,
    input logic [31:0] in_sample,
    input logic [31:0] in_noise,
    input logic [31:0] in_sigma,
    input logic [31:0] in_sigma_interpol,
    input logic [31:0] in_sigma_down,
    input logic [31:0] in_sigma_up,
    output logic out_valid,
    input logic out_ready,
    output logic [31:0] out_prev_sample,
    output logic [31:0] out_pred_original_sample,
    output logic [1:0] out_error
);
    logic busy,pending;
    logic signed [31:0] saved_sample;
    logic [31:0] sigma,interpol,down,up;
    reg signed [127:0] model,sample,noise,ss,si,sd,su,predicted,previous;
    function automatic [31:0] quantize(input signed [127:0] value);
        reg signed [127:0] rounded;
        begin
            rounded=value >>> 16;
            if(rounded>2147483647) quantize=32'h7fffffff;
            else if(rounded < -128'sd2147483648) quantize=32'h80000000;
            else quantize=rounded[31:0];
        end
    endfunction
    assign in_ready=!busy || out_ready;
    assign out_valid=busy;
    always_ff @(posedge clk) begin
        if(rst) begin
            busy<=0;pending<=0;saved_sample<=0;sigma<=0;interpol<=0;down<=0;up<=0;
            out_prev_sample<=0;out_pred_original_sample<=0;out_error<=0;
        end else if(in_valid && in_ready) begin
            busy<=1;
            out_error<=0;out_prev_sample<=0;out_pred_original_sample<=0;
            model={{96{in_model_output[31]}},in_model_output};
            sample={{96{in_sample[31]}},in_sample};
            noise={{96{in_noise[31]}},in_noise};
            if(in_phase!=pending) out_error<=1;
            else if(!in_phase) begin
                if(in_sigma==0 || in_sigma_interpol==0 || in_sigma_interpol>in_sigma || in_sigma_down>in_sigma_interpol)
                    out_error<=2;
                else begin
                    saved_sample<=in_sample;
                    sigma<=in_sigma;interpol<=in_sigma_interpol;down<=in_sigma_down;up<=in_sigma_up;
                    ss={96'd0,in_sigma};si={96'd0,in_sigma_interpol};
                    predicted=sample*65536-ss*model;
                    previous=sample*65536+model*(si-ss);
                    out_pred_original_sample<=quantize(predicted);
                    out_prev_sample<=quantize(previous);
                    pending<=1;
                end
            end else begin
                ss={96'd0,sigma};si={96'd0,interpol};sd={96'd0,down};su={96'd0,up};
                predicted=sample*65536-si*model;
                previous=$signed(saved_sample)*128'sd65536+model*(sd-ss)+noise*su;
                out_pred_original_sample<=quantize(predicted);
                out_prev_sample<=quantize(previous);
                pending<=0;
            end
        end else if(out_ready) busy<=0;
    end
endmodule
