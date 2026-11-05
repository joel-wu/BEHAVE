module freq_div (
    input logic CLK_in,
    input logic RST,
    output logic CLK_50,
    output logic CLK_10,
    output logic CLK_1
);
    logic [3:0] cnt_10;
    logic [6:0] cnt_100;
    always_ff @(posedge CLK_in or posedge RST) begin
        if (RST) begin
            CLK_50 <= 1'b0;
            CLK_10 <= 1'b0;
            CLK_1 <= 1'b0;
            cnt_10 <= 4'd0;
            cnt_100 <= 7'd0;
        end else begin
            CLK_50 <= ~CLK_50;
            if (cnt_10 == 4'd4) begin
                cnt_10 <= 4'd0;
                CLK_10 <= ~CLK_10;
            end else cnt_10 <= cnt_10 + 4'd1;
            if (cnt_100 == 7'd49) begin
                cnt_100 <= 7'd0;
                CLK_1 <= ~CLK_1;
            end else cnt_100 <= cnt_100 + 7'd1;
        end
    end
endmodule
