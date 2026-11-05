module digital_dice_roller (
    input logic clk, reset_n, button,
    output logic [2:0] dice_value
);
    logic rolling;
    logic [2:0] counter;
    always_ff @(posedge clk or negedge reset_n) begin
        if (!reset_n) begin rolling<=0; counter<=0; dice_value<=0; end
        else if (button) begin
            rolling<=1;
            if (!rolling || counter==6) counter<=1;
            else counter<=counter+1'b1;
        end else if (rolling) begin
            dice_value<=counter;
            counter<=0; rolling<=0;
        end
    end
endmodule
