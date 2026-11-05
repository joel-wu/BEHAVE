module calendar (
    input logic CLK,
    input logic RST,
    output logic [5:0] Hours,
    output logic [5:0] Mins,
    output logic [5:0] Secs
);
    always_ff @(posedge CLK or posedge RST) begin
        if (RST) begin
            Hours <= 6'd0;
            Mins <= 6'd0;
            Secs <= 6'd0;
        end else if (Secs == 6'd59) begin
            Secs <= 6'd0;
            if (Mins == 6'd59) begin
                Mins <= 6'd0;
                Hours <= Hours == 6'd23 ? 6'd0 : Hours + 6'd1;
            end else Mins <= Mins + 6'd1;
        end else Secs <= Secs + 6'd1;
    end
endmodule
