module ai_ml_sample_pick_sample_multinomial_cdf (
    input  logic        clk,
    input  logic        rst,

    input  logic        in_valid,
    output logic        in_ready,
    input  logic [31:0] in_sample,
    input  logic [32:0] in_cdf0,
    input  logic [32:0] in_cdf1,
    input  logic [32:0] in_cdf2,
    input  logic [32:0] in_cdf3,
    input  logic [32:0] in_cdf4,
    input  logic [32:0] in_cdf5,
    input  logic [32:0] in_cdf6,
    input  logic [32:0] in_cdf7,

    output logic        out_valid,
    input  logic        out_ready,
    output logic        out_error,
    output logic [2:0]  out_token_id
);
  logic [32:0] cdf [0:7];
  logic error, found;
  logic [2:0] token;
  logic [32:0] previous;
  integer i;
  always_comb begin
    cdf[0]=in_cdf0;
    cdf[1]=in_cdf1;
    cdf[2]=in_cdf2;
    cdf[3]=in_cdf3;
    cdf[4]=in_cdf4;
    cdf[5]=in_cdf5;
    cdf[6]=in_cdf6;
    cdf[7]=in_cdf7;
    error=(cdf[7]!=33'd4294967296);
    previous=0;
    found=0;
    token=0;
    for(i=0;i<8;i=i+1) begin
      if(cdf[i]<previous || cdf[i]>33'd4294967296) error=1;
      previous=cdf[i];
      if(!found && {1'b0,in_sample}<cdf[i]) begin token=i[2:0]; found=1; end
    end
    if(error) token=0;
  end

  assign in_ready = !rst && (!out_valid || out_ready);
  always_ff @(posedge clk) begin
    if (rst) begin
      out_valid <= 0;
      out_token_id <= '0;
      out_error <= '0;
    end else if (in_valid && in_ready) begin
      out_valid <= 1;
      out_token_id<=token;
      out_error<=error;
    end else if (out_ready) out_valid <= 0;
  end
endmodule
