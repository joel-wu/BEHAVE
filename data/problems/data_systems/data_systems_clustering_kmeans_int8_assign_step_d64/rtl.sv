module data_systems_clustering_kmeans_int8_assign_step_d64 (
    input logic clk, rst,
    input logic in_valid,
    output logic in_ready,
    input logic [511:0] in_vector,
    input logic [4095:0] in_centroids,
    output logic out_valid,
    input logic out_ready,
    output logic [31:0] out_assign,
    output logic [21:0] out_distance
);
  integer c, j, difference, x, center;
  logic [21:0] distance, best;
  logic [31:0] winner;
  always_comb begin
    best=22'd4194303;
    winner=0;
    distance=0;
    difference=0;
    x=0;
    center=0;
    for(c=0;c<8;c=c+1) begin
      distance=0;
      for(j=0;j<64;j=j+1) begin
        x=$signed(in_vector[8*j +: 8]);
        center=$signed(in_centroids[8*(64*c+j) +: 8]);
        difference=x-center;
        distance=distance+difference*difference;
      end
      if(distance<best) begin best=distance; winner=c; end
    end
  end
  assign in_ready=!rst && (!out_valid || out_ready);
  always_ff @(posedge clk) begin
    if(rst) begin out_valid<=0; out_assign<=0; out_distance<=0; end
    else if(in_valid && in_ready) begin
      out_valid<=1; out_assign<=winner; out_distance<=best;
    end else if(out_ready) out_valid<=0;
  end
endmodule
