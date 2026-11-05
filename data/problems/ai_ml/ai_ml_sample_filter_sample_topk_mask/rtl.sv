module ai_ml_sample_filter_sample_topk_mask (
    input  logic         clk,
    input  logic         rst,

    input  logic         in_valid,
    output logic         in_ready,
    input  logic [7:0]   in_k,
    input  logic [15:0]  in_logits_0,
    input  logic [15:0]  in_logits_1,
    input  logic [15:0]  in_logits_2,
    input  logic [15:0]  in_logits_3,
    input  logic [15:0]  in_logits_4,
    input  logic [15:0]  in_logits_5,
    input  logic [15:0]  in_logits_6,
    input  logic [15:0]  in_logits_7,
    input  logic [15:0]  in_logits_8,
    input  logic [15:0]  in_logits_9,
    input  logic [15:0]  in_logits_10,
    input  logic [15:0]  in_logits_11,
    input  logic [15:0]  in_logits_12,
    input  logic [15:0]  in_logits_13,
    input  logic [15:0]  in_logits_14,
    input  logic [15:0]  in_logits_15,
    input  logic         in_mask_0,
    input  logic         in_mask_1,
    input  logic         in_mask_2,
    input  logic         in_mask_3,
    input  logic         in_mask_4,
    input  logic         in_mask_5,
    input  logic         in_mask_6,
    input  logic         in_mask_7,
    input  logic         in_mask_8,
    input  logic         in_mask_9,
    input  logic         in_mask_10,
    input  logic         in_mask_11,
    input  logic         in_mask_12,
    input  logic         in_mask_13,
    input  logic         in_mask_14,
    input  logic         in_mask_15,

    output logic         out_valid,
    input  logic         out_ready,
    output logic         out_keep_0,
    output logic         out_keep_1,
    output logic         out_keep_2,
    output logic         out_keep_3,
    output logic         out_keep_4,
    output logic         out_keep_5,
    output logic         out_keep_6,
    output logic         out_keep_7,
    output logic         out_keep_8,
    output logic         out_keep_9,
    output logic         out_keep_10,
    output logic         out_keep_11,
    output logic         out_keep_12,
    output logic         out_keep_13,
    output logic         out_keep_14,
    output logic         out_keep_15
);
  logic signed [15:0] scores [0:15];
  logic [15:0] excluded, keep;
  integer i, j, greater;
  always_comb begin
    scores[0] = $signed(in_logits_0);
    excluded[0] = in_mask_0;
    scores[1] = $signed(in_logits_1);
    excluded[1] = in_mask_1;
    scores[2] = $signed(in_logits_2);
    excluded[2] = in_mask_2;
    scores[3] = $signed(in_logits_3);
    excluded[3] = in_mask_3;
    scores[4] = $signed(in_logits_4);
    excluded[4] = in_mask_4;
    scores[5] = $signed(in_logits_5);
    excluded[5] = in_mask_5;
    scores[6] = $signed(in_logits_6);
    excluded[6] = in_mask_6;
    scores[7] = $signed(in_logits_7);
    excluded[7] = in_mask_7;
    scores[8] = $signed(in_logits_8);
    excluded[8] = in_mask_8;
    scores[9] = $signed(in_logits_9);
    excluded[9] = in_mask_9;
    scores[10] = $signed(in_logits_10);
    excluded[10] = in_mask_10;
    scores[11] = $signed(in_logits_11);
    excluded[11] = in_mask_11;
    scores[12] = $signed(in_logits_12);
    excluded[12] = in_mask_12;
    scores[13] = $signed(in_logits_13);
    excluded[13] = in_mask_13;
    scores[14] = $signed(in_logits_14);
    excluded[14] = in_mask_14;
    scores[15] = $signed(in_logits_15);
    excluded[15] = in_mask_15;
    keep = 0;
    greater = 0;
    for (i=0; i<16; i=i+1) begin
      greater = 0;
      for (j=0; j<16; j=j+1)
        if (!excluded[j] && scores[j] > scores[i]) greater = greater + 1;
      keep[i] = !excluded[i] && in_k != 0 && greater < in_k;
    end
  end

  assign in_ready = !rst && (!out_valid || out_ready);
  always_ff @(posedge clk) begin
    if (rst) begin
      out_valid <= 0;
      out_keep_0 <= '0;
      out_keep_1 <= '0;
      out_keep_2 <= '0;
      out_keep_3 <= '0;
      out_keep_4 <= '0;
      out_keep_5 <= '0;
      out_keep_6 <= '0;
      out_keep_7 <= '0;
      out_keep_8 <= '0;
      out_keep_9 <= '0;
      out_keep_10 <= '0;
      out_keep_11 <= '0;
      out_keep_12 <= '0;
      out_keep_13 <= '0;
      out_keep_14 <= '0;
      out_keep_15 <= '0;
    end else if (in_valid && in_ready) begin
      out_valid <= 1;
      out_keep_0 <= keep[0];
      out_keep_1 <= keep[1];
      out_keep_2 <= keep[2];
      out_keep_3 <= keep[3];
      out_keep_4 <= keep[4];
      out_keep_5 <= keep[5];
      out_keep_6 <= keep[6];
      out_keep_7 <= keep[7];
      out_keep_8 <= keep[8];
      out_keep_9 <= keep[9];
      out_keep_10 <= keep[10];
      out_keep_11 <= keep[11];
      out_keep_12 <= keep[12];
      out_keep_13 <= keep[13];
      out_keep_14 <= keep[14];
      out_keep_15 <= keep[15];

    end else if (out_ready) out_valid <= 0;
  end
endmodule
