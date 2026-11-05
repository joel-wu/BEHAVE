module palindrome_detect #(parameter int N = 3) (
    input logic clk, reset, bit_stream,
    output logic palindrome_detected
);
    logic [2:0] window;
    logic [1:0] count;
    assign palindrome_detected=(count==3) && (window[2]==window[0]);
    always_ff @(posedge clk) begin
        if (reset) begin window<=0; count<=0; end
        else begin
            window<={window[1:0],bit_stream};
            if (count<3) count<=count+1'b1;
        end
    end
endmodule
