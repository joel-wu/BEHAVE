module ai_ml_audio_rnnt_rnnt_joint_network #(
    parameter int ENCODING_DIM = 4,
    parameter int OUTPUT_DIM   = 4
) (
    input  logic clk,
    input  logic rst,

    input  logic in_valid,
    output logic in_ready,
    input  logic [16*ENCODING_DIM-1:0] in_source_encodings,
    input  logic [16*ENCODING_DIM-1:0] in_target_encodings,
    input  logic [15:0] in_source_length,
    input  logic [15:0] in_target_length,
    input  logic [16*OUTPUT_DIM*ENCODING_DIM-1:0] in_weight,
    input  logic [16*OUTPUT_DIM-1:0] in_bias,

    output logic out_valid,
    input  logic out_ready,
    output logic [16*OUTPUT_DIM-1:0] out_output,
    output logic [15:0] out_source_length,
    output logic [15:0] out_target_length
);

logic busy;
wire input_accepted;
wire output_consumed;

assign input_accepted  = in_valid & in_ready;
assign output_consumed = out_valid & out_ready;

assign in_ready  = rst ? 1'b1 : (~busy | output_consumed);
assign out_valid = rst ? 1'b0 : busy;

integer o, i;
logic signed [15:0] source_i;
logic signed [15:0] target_i;
logic signed [16:0] joint_i;
logic signed [16:0] act_i;
logic signed [15:0] bias_o;
logic signed [15:0] weight_o_i;
logic signed [15:0] sum16_o;
logic signed [32:0] prod_i;
logic signed [31:0] accum_o;
logic [16*OUTPUT_DIM-1:0] computed_out_output;

always_comb begin
    computed_out_output = '0;

    for (o = 0; o < OUTPUT_DIM; o = o + 1) begin
        bias_o  = $signed(in_bias[16*o +: 16]);
        accum_o = $signed(bias_o);

        for (i = 0; i < ENCODING_DIM; i = i + 1) begin
            source_i = $signed(in_source_encodings[16*i +: 16]);
            target_i = $signed(in_target_encodings[16*i +: 16]);
            joint_i  = source_i + target_i;

            if (joint_i < 0)
                act_i = 16'sd0;
            else
                act_i = joint_i;

            weight_o_i = $signed(in_weight[16*(o*ENCODING_DIM + i) +: 16]);
            prod_i     = act_i * weight_o_i;
            accum_o    = accum_o + prod_i;
        end

        sum16_o = accum_o[15:0];
        computed_out_output[16*o +: 16] = sum16_o;
    end
end

always_ff @(posedge clk or posedge rst) begin
    if (rst) begin
        busy              <= 1'b0;
        out_output        <= '0;
        out_source_length <= 16'd0;
        out_target_length <= 16'd0;
    end else if (input_accepted) begin
        busy              <= 1'b1;
        out_output        <= computed_out_output;
        out_source_length <= in_source_length;
        out_target_length <= in_target_length;
    end else if (output_consumed) begin
        busy <= 1'b0;
    end
end

endmodule
