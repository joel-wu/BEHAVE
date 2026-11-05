module sigma_delta_audio (
    input logic clk_sig_level, clk_en_sig,
    input logic [14:0] load_data_sum, read_data_sum,
    output logic left_sig, right_sig
);
    logic [1:0] phase=0;
    logic signed [14:0] l_sample=0, r_sample=0, l_target=0, r_target=0;
    logic signed [15:0] l_slope=0, r_slope=0;
    logic signed [16:0] l_ac1=0, r_ac1=0;
    logic signed [19:0] l_ac2=0, r_ac2=0;
    logic signed [14:0] l_feedback=0, r_feedback=0;
    logic [15:0] seed_1=1, seed_2=2;
    wire signed [15:0] l_difference=$signed(load_data_sum)-$signed(l_sample);
    wire signed [15:0] r_difference=$signed(read_data_sum)-$signed(r_sample);
    wire signed [15:0] l_step=l_difference >>> 2;
    wire signed [15:0] r_step=r_difference >>> 2;
    wire signed [14:0] l_next_sample=(phase==3) ? l_target : l_sample+((phase==0) ? l_step : l_slope);
    wire signed [14:0] r_next_sample=(phase==3) ? r_target : r_sample+((phase==0) ? r_step : r_slope);
    wire signed [19:0] noise=(seed_1[0]^seed_2[0]) ? 20'sd1 : -20'sd1;
    wire signed [16:0] l1=l_ac1+l_next_sample-l_feedback;
    wire signed [16:0] r1=r_ac1+r_next_sample-r_feedback;
    wire signed [19:0] l2=l_ac2+l1-l_feedback+noise;
    wire signed [19:0] r2=r_ac2+r1-r_feedback-noise;
    initial begin left_sig=0; right_sig=0; end
    always_ff @(posedge clk_sig_level) if(clk_en_sig) begin
        if(phase==0) begin
            l_target<=load_data_sum; r_target<=read_data_sum;
            l_slope<=l_step; r_slope<=r_step;
        end
        l_sample<=l_next_sample; r_sample<=r_next_sample;
        l_ac1<=l1; r_ac1<=r1; l_ac2<=l2; r_ac2<=r2;
        left_sig<=!l2[19]; right_sig<=!r2[19];
        l_feedback<=l2[19] ? -15'sd16384 : 15'sd16383;
        r_feedback<=r2[19] ? -15'sd16384 : 15'sd16383;
        seed_1<={seed_1[14:0],seed_1[15]^seed_1[13]^seed_1[12]^seed_1[10]};
        seed_2<={seed_2[14:0],seed_2[15]^seed_2[14]^seed_2[12]^seed_2[3]};
        phase<=phase+1'b1;
    end
endmodule
