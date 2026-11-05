module signal_processing_isp_aux_eeh (
    input  logic        clk,
    input  logic        rst,

    input  logic        in_valid,
    output logic        in_ready,
    input  logic [7:0]  in_p00,
    input  logic [7:0]  in_p01,
    input  logic [7:0]  in_p02,
    input  logic [7:0]  in_p03,
    input  logic [7:0]  in_p04,
    input  logic [7:0]  in_p10,
    input  logic [7:0]  in_p11,
    input  logic [7:0]  in_p12,
    input  logic [7:0]  in_p13,
    input  logic [7:0]  in_p14,
    input  logic [7:0]  in_p20,
    input  logic [7:0]  in_p21,
    input  logic [7:0]  in_p22,
    input  logic [7:0]  in_p23,
    input  logic [7:0]  in_p24,

    input  logic signed [7:0]  in_f00,
    input  logic signed [7:0]  in_f01,
    input  logic signed [7:0]  in_f02,
    input  logic signed [7:0]  in_f03,
    input  logic signed [7:0]  in_f04,
    input  logic signed [7:0]  in_f10,
    input  logic signed [7:0]  in_f11,
    input  logic signed [7:0]  in_f12,
    input  logic signed [7:0]  in_f13,
    input  logic signed [7:0]  in_f14,
    input  logic signed [7:0]  in_f20,
    input  logic signed [7:0]  in_f21,
    input  logic signed [7:0]  in_f22,
    input  logic signed [7:0]  in_f23,
    input  logic signed [7:0]  in_f24,

    input  logic signed [15:0] in_gain0,
    input  logic signed [15:0] in_gain1,
    input  logic signed [15:0] in_thres0,
    input  logic signed [15:0] in_thres1,
    input  logic signed [15:0] in_emclip0,
    input  logic signed [15:0] in_emclip1,

    output logic        out_valid,
    input  logic        out_ready,
    output logic [7:0]  out_ee_pixel,
    output logic [16:0] out_em_value
);

logic busy;
logic signed [31:0] pixels [0:14], coefficients [0:14];
logic signed [31:0] edge_sum, edge_value, threshold0, threshold1;
logic signed [63:0] gain0, gain1, lower_q8, upper_q8;
logic signed [63:0] correction_q8, bounded_q8, enhanced;
logic [7:0] next_pixel;

assign in_ready = !busy || out_ready;
assign out_valid = busy;

always_comb begin
    pixels[0] = {24'b0, in_p00};
    coefficients[0] = in_f00;
    pixels[1] = {24'b0, in_p01};
    coefficients[1] = in_f01;
    pixels[2] = {24'b0, in_p02};
    coefficients[2] = in_f02;
    pixels[3] = {24'b0, in_p03};
    coefficients[3] = in_f03;
    pixels[4] = {24'b0, in_p04};
    coefficients[4] = in_f04;
    pixels[5] = {24'b0, in_p10};
    coefficients[5] = in_f10;
    pixels[6] = {24'b0, in_p11};
    coefficients[6] = in_f11;
    pixels[7] = {24'b0, in_p12};
    coefficients[7] = in_f12;
    pixels[8] = {24'b0, in_p13};
    coefficients[8] = in_f13;
    pixels[9] = {24'b0, in_p14};
    coefficients[9] = in_f14;
    pixels[10] = {24'b0, in_p20};
    coefficients[10] = in_f20;
    pixels[11] = {24'b0, in_p21};
    coefficients[11] = in_f21;
    pixels[12] = {24'b0, in_p22};
    coefficients[12] = in_f22;
    pixels[13] = {24'b0, in_p23};
    coefficients[13] = in_f23;
    pixels[14] = {24'b0, in_p24};
    coefficients[14] = in_f24;
    edge_sum = 0;
    for (integer i=0; i<15; i=i+1)
        edge_sum = edge_sum + pixels[i]*coefficients[i];
    edge_value = edge_sum / 32'sd8;
    threshold0 = in_thres0;
    threshold1 = in_thres1;
    gain0 = in_gain0;
    gain1 = in_gain1;
    lower_q8 = in_emclip0;
    upper_q8 = in_emclip1;
    lower_q8 = lower_q8 <<< 8;
    upper_q8 = upper_q8 <<< 8;

    if (edge_value < -threshold1)
        correction_q8 = gain1*edge_value;
    else if (edge_value < -threshold0 && edge_value > -threshold1)
        correction_q8 = 0;
    else if (edge_value < threshold0 && edge_value > -threshold1)
        correction_q8 = gain0*edge_value;
    else if (edge_value > threshold0 && edge_value < threshold1)
        correction_q8 = 0;
    else if (edge_value > threshold1)
        correction_q8 = gain1*edge_value;
    else correction_q8 = 0;

    bounded_q8 = correction_q8 > upper_q8 ? upper_q8 : correction_q8;
    if (bounded_q8 < lower_q8) bounded_q8 = lower_q8;
    enhanced = pixels[7];
    enhanced = ((enhanced <<< 8) + bounded_q8) >>> 8;
    if (enhanced < 0) next_pixel = 8'd0;
    else if (enhanced > 255) next_pixel = 8'd255;
    else next_pixel = enhanced[7:0];
end

always_ff @(posedge clk or posedge rst) begin
    if (rst) begin
        busy <= 1'b0;
        out_ee_pixel <= 8'd0;
        out_em_value <= 17'd0;
    end else if (in_valid && in_ready) begin
        busy <= 1'b1;
        out_ee_pixel <= next_pixel;
        out_em_value <= edge_value[16:0];
    end else if (out_valid && out_ready) begin
        busy <= 1'b0;
        out_ee_pixel <= 8'd0;
        out_em_value <= 17'd0;
    end
end
endmodule
