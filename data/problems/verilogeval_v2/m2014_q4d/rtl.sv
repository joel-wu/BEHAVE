module TopModule (input logic clk, init_reset, value, output logic out);
    always_ff @(posedge clk) if(init_reset) out<=0; else out<=out^value;
endmodule
