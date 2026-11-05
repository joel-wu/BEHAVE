module TopModule(input logic clock_level, a, output logic p, q);
    initial begin p=0; q=0; end
    always_latch if(clock_level) p=a;
    always_ff @(negedge clock_level) q<=p;
endmodule
