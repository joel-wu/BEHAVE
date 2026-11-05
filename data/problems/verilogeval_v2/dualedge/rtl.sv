module TopModule(input logic clock_level, d, output logic q);
    logic positive=0, negative=0;
    always_ff @(posedge clock_level) positive<=d;
    always_ff @(negedge clock_level) negative<=d;
    always_comb q=clock_level ? positive : negative;
endmodule
