module square_wave(input logic clk, init_reset, input logic [7:0] freq, output logic wave_out);
    logic [7:0] count;
    always_ff @(posedge clk) begin
        if(init_reset) begin count<=0; wave_out<=0; end
        else if(freq!=0 && count==freq-8'd1) begin count<=0; wave_out<=!wave_out; end
        else count<=count+8'd1;
    end
endmodule
