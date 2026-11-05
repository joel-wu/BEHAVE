module TopModule (input logic clk, init_reset, clear, input logic [31:0] value, output logic [31:0] out);
    logic [31:0] previous;
    always_ff @(posedge clk) begin
        if (init_reset) begin previous<=0; out<=0; end
        else begin
            previous<=value;
            if (clear) out<=0;
            else out<=out | (previous & ~value);
        end
    end
endmodule
