module ai_ml_sample_filter_sample_topp_mask (
    input  logic         clk,
    input  logic         rst,

    input  logic         in_valid,
    output logic         in_ready,
    input  logic [16:0]  in_top_p,
    input  logic [127:0] in_logits,

    output logic         out_valid,
    input  logic         out_ready,
    output logic [7:0]   out_mask
);
  logic signed [15:0] score [0:7];
  logic signed [16:0] high, delta;
  logic [32:0] weight [0:7];
  logic [35:0] den, cumulative;
  logic [52:0] lhs, rhs;
  logic [16:0] p;
  logic [7:0] mask;
  integer i, j, count;
  function automatic [32:0] exp_word(input logic [16:0] d);
    case(d)
      17'd0: exp_word=33'd4294967296;
      17'd1: exp_word=33'd1580030169;
      17'd2: exp_word=33'd581260615;
      17'd3: exp_word=33'd213833830;
      17'd4: exp_word=33'd78665070;
      17'd5: exp_word=33'd28939262;
      17'd6: exp_word=33'd10646160;
      17'd7: exp_word=33'd3916503;
      17'd8: exp_word=33'd1440801;
      17'd9: exp_word=33'd530041;
      17'd10: exp_word=33'd194991;
      17'd11: exp_word=33'd71733;
      17'd12: exp_word=33'd26389;
      17'd13: exp_word=33'd9708;
      17'd14: exp_word=33'd3571;
      17'd15: exp_word=33'd1314;
      17'd16: exp_word=33'd483;
      17'd17: exp_word=33'd178;
      17'd18: exp_word=33'd65;
      17'd19: exp_word=33'd24;
      17'd20: exp_word=33'd9;
      17'd21: exp_word=33'd3;
      17'd22: exp_word=33'd1;
      default: exp_word=0;
    endcase
  endfunction
  always_comb begin
    for (i=0;i<8;i=i+1) score[i]=$signed(in_logits[16*i +: 16]);
    high=$signed(score[0]);
    for (i=1;i<8;i=i+1) if ($signed(score[i])>high) high=$signed(score[i]);
    den=0;
    delta=0;
    for (i=0;i<8;i=i+1) begin
      delta=high-$signed(score[i]);
      weight[i]=exp_word($unsigned(delta));
      den=den+{3'b0,weight[i]};
    end
    p=in_top_p>17'd65536 ? 17'd65536 : in_top_p;
    rhs={36'b0,(17'd65536-p)}*{17'b0,den};
    mask=0;
    cumulative=0;
    count=0;
    lhs=0;
    for(i=0;i<8;i=i+1) begin
      cumulative=0;
      count=0;
      for(j=0;j<8;j=j+1) begin
        if(score[j]<score[i] || (score[j]==score[i] && j<=i)) begin
          cumulative=cumulative+{3'b0,weight[j]};
          count=count+1;
        end
      end
      lhs={1'b0,cumulative,16'b0};
      mask[i]=(count==8) || (lhs>rhs);
    end
  end

  assign in_ready = !rst && (!out_valid || out_ready);
  always_ff @(posedge clk) begin
    if (rst) begin
      out_valid <= 0;
      out_mask <= '0;
    end else if (in_valid && in_ready) begin
      out_valid <= 1;
      out_mask <= mask;
    end else if (out_ready) out_valid <= 0;
  end
endmodule
