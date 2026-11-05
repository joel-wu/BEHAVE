module TopModule(input logic d, ena, output logic q);
    initial q=0;
    always_latch if(ena) q=d;
endmodule
