module car_parking_system #(
    parameter int TOTAL_SPACES = 12
) (
    input logic clk, reset, vehicle_entry_sensor, vehicle_exit_sensor,
    output logic [$clog2(TOTAL_SPACES+1)-1:0] available_spaces, count_car,
    output logic led_status,
    output logic [6:0] seven_seg_display_available_tens,
    output logic [6:0] seven_seg_display_available_units,
    output logic [6:0] seven_seg_display_count_tens,
    output logic [6:0] seven_seg_display_count_units
);
    localparam logic [1:0] IDLE=0, ENTRY=1, EXIT=2, FULL=3;
    logic [1:0] state;
    logic previous_entry, previous_exit;
    function automatic [6:0] segments(input integer digit);
        case (digit)
            0: segments=7'b1111110; 1: segments=7'b0110000;
            2: segments=7'b1101101; 3: segments=7'b1111001;
            4: segments=7'b0110011; 5: segments=7'b1011011;
            6: segments=7'b1011111; 7: segments=7'b1110000;
            8: segments=7'b1111111; 9: segments=7'b1111011;
            default: segments=0;
        endcase
    endfunction
    always_comb begin
        available_spaces = TOTAL_SPACES-count_car;
        led_status = count_car<TOTAL_SPACES;
        seven_seg_display_available_tens = segments(available_spaces/10);
        seven_seg_display_available_units = segments(available_spaces%10);
        seven_seg_display_count_tens = segments(count_car/10);
        seven_seg_display_count_units = segments(count_car%10);
    end
    always_ff @(posedge clk or posedge reset) begin
        if (reset) begin
            state<=IDLE; count_car<=0; previous_entry<=0; previous_exit<=0;
        end else begin
            previous_entry<=vehicle_entry_sensor;
            previous_exit<=vehicle_exit_sensor;
            case (state)
                ENTRY: begin
                    count_car<=count_car+1'b1;
                    state<=(count_car==TOTAL_SPACES-1) ? FULL : IDLE;
                end
                EXIT: begin count_car<=count_car-1'b1; state<=IDLE; end
                default: begin
                    if (vehicle_exit_sensor && !previous_exit && count_car!=0) state<=EXIT;
                    else if (vehicle_entry_sensor && !previous_entry && count_car<TOTAL_SPACES) state<=ENTRY;
                end
            endcase
        end
    end
endmodule
