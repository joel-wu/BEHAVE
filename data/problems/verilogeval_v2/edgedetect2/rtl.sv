module TopModule (input logic clk, init_reset, input logic [7:0] value, output logic [7:0] anyedge);
    logic [7:0] previous;
    always_ff @(posedge clk) begin
        if(init_reset) begin previous<=0; anyedge<=0; end
        else begin previous<=value; anyedge<=value^previous; end
    end
endmodule
